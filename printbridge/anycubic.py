"""Anycubic Kobra 3 / 3 Max / S1 / S1 Max / 4 / X com o firmware de FÁBRICA, pelo "Modo LAN".

Basta o IP: a impressora entrega sozinha as credenciais do MQTT local, sem conta, senha nem
Rinkhals. Protocolo como descrito pelos projetos da comunidade (anycubic_ha_local, kobra-connect,
anycubic-orca-plugin, kobra-lan-cli — fontes em MARCAS-PROTOCOLOS.md):

1. GET  http://IP:18910/info → {token, cn, modelId, modelName, ctrlType ("lan"/"cloud"), ctrlInfoUrl}
2. POST {ctrlInfoUrl}?ts=&nonce=&sign=&did=, com sign = md5(md5(token[:16]) + ts + nonce).
   A chave é o token que a própria impressora acabou de mandar (nada vem do Slicer da Anycubic).
3. Resposta {code: 200, data: {token, info}}: "info" é AES-CBC (chave token[16:32], IV = data.token)
   → {broker "mqtts://IP:9883", username, password, deviceId}.
4. MQTT com TLS na 9883 (certificado próprio da impressora; sem certificado de cliente).
   Pedidos em  anycubic/anycubicCloud/v1/web/printer/{modelId}/{deviceId}/{tipo}
   Respostas em anycubic/anycubicCloud/v1/printer/public/{modelId}/{deviceId}/{tipo}/report
   Corpo: {type, action, timestamp, msgid, data}.
- Envio de arquivo: POST multipart (campos "filename" e "gcode") no urls.fileUploadurl do
  relatório "info" (http://IP:18910/gcode_upload?s=…); depois print/start pelo tópico "slicer".
- Câmera: video/startCapture → H.264 em HTTP-FLV (porta 18088), convertido em JPEG pelo PyAV.

Mesma "cara" da BambuPrinter (summary/command/start/stop). Kobra 2 Pro/Plus/Max usam outro
handshake (sem assinatura), ainda não suportado: nelas, Rinkhals + Klipper.

NÃO TESTADO EM IMPRESSORA REAL — só contra um simulador escrito a partir do protocolo público.
"""

import base64
import hashlib
import io
import json
import os
import random
import re
import ssl
import string
import threading
import time
import urllib.parse
import urllib.request
import uuid

import paho.mqtt.client as mqtt

PORTA_INFO = 18910
RAIZ = "anycubic/anycubicCloud/v1"
# velocidade do painel (igual à Bambu) → print_speed_mode (1 silencioso · 2 padrão · 3 esportivo)
VELOCIDADES = {1: 1, 2: 2, 3: 3, 4: 3}
ESTADOS = {"preheating": "PREPARE", "auto_leveling": "PREPARE", "vibrating": "PREPARE",
           "flow_calibrating": "PREPARE", "printing": "RUNNING", "resuming": "RUNNING",
           "resumed": "RUNNING", "updated": "RUNNING", "pausing": "PAUSE", "paused": "PAUSE",
           "stopping": "IDLE", "stoped": "IDLE", "finished": "FINISH", "failed": "FAILED"}
COM_LUZ = {"20025", "20029"}  # S1 e S1 Max: luz da câmara (as abertas não têm)


class ErroAnycubic(Exception):
    pass


class ModoNuvem(ErroAnycubic):
    pass


class _SemRedirecionar(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None  # o pedido termina no IP que a pessoa digitou


_abridor = urllib.request.build_opener(_SemRedirecionar)


def _pedir(metodo: str, url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, method=metodo, data=b"" if metodo == "POST" else None)
    with _abridor.open(req, timeout=timeout) as r:
        d = json.loads(r.read() or b"{}")
    if not isinstance(d, dict):
        raise ErroAnycubic("o aparelho nesse IP não respondeu como uma Anycubic")
    return d


def assinar(token: str, ts: int, nonce: str) -> str:
    primeiro = hashlib.md5(token[:16].encode()).hexdigest()
    return hashlib.md5((primeiro + str(ts) + nonce).encode()).hexdigest()


def _abrir_credenciais(cifrado: str, token: str, token_local: str) -> dict:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    iv = token_local.encode()[:16].ljust(16, b"\0")
    d = Cipher(algorithms.AES(token[16:32].encode()), modes.CBC(iv)).decryptor()
    bruto = d.update(base64.b64decode(cifrado)) + d.finalize()
    u = padding.PKCS7(128).unpadder()
    return json.loads(u.update(bruto) + u.finalize())


def _no_ip(ip: str, url: str | None, padrao: str) -> str:
    """Usa só porta/caminho do endereço que a impressora informa; o IP é sempre o digitado."""
    try:
        u = urllib.parse.urlsplit(url or "")
        if u.scheme not in ("http", "https") or not u.hostname:
            return padrao
        return f"{u.scheme}://{ip}:{u.port or PORTA_INFO}{u.path or '/'}" + (f"?{u.query}" if u.query else "")
    except ValueError:
        return padrao


def ler_info(ip: str, timeout: float = 6) -> dict:
    return _pedir("GET", f"http://{ip}:{PORTA_INFO}/info", timeout)


def handshake(ip: str, timeout: float = 6) -> dict:
    """/info → /ctrl assinado → credenciais do MQTT local. Levanta ModoNuvem / ErroAnycubic."""
    info = ler_info(ip, timeout)
    if info.get("ctrlType") == "cloud":
        raise ModoNuvem("a impressora está no modo nuvem: ligue o Modo LAN na tela dela")
    token, ctrl, modelo = info.get("token"), info.get("ctrlInfoUrl"), info.get("modelId")
    if not (isinstance(token, str) and len(token) >= 32 and isinstance(ctrl, str) and modelo):
        raise ErroAnycubic("esta impressora não usa o Modo LAN assinado (Kobra 3 / S1 / 4 / X)")
    ts = int(time.time() * 1000)
    nonce = "".join(random.choices(string.ascii_letters + string.digits, k=6))
    did = "".join(random.choices(string.ascii_uppercase + string.digits, k=32))
    qs = urllib.parse.urlencode({"ts": ts, "nonce": nonce, "sign": assinar(token, ts, nonce), "did": did})
    url = _no_ip(ip, ctrl, f"http://{ip}:{PORTA_INFO}/ctrl")
    r = _pedir("POST", url + ("&" if "?" in url else "?") + qs, timeout)
    if r.get("code") != 200:
        raise ErroAnycubic(f"a impressora recusou a conexão: {r.get('message') or r.get('code')}")
    selado = r.get("data") or {}
    try:
        d = _abrir_credenciais(selado["info"], token, selado["token"])
        porta = int(re.match(r"mqtts?://[^:/]+:(\d+)", d["broker"]).group(1))
        return {"porta": porta, "usuario": d["username"], "senha": d["password"], "device_id": d["deviceId"],
                "model_id": str(modelo), "cn": str(info.get("cn") or ""),
                "nome": info.get("modelName") or "Anycubic"}
    except Exception as e:
        raise ErroAnycubic(f"não consegui abrir as credenciais do Modo LAN ({type(e).__name__})") from e


def _cliente(cred: dict, nome: str) -> mqtt.Client:
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"printbridge-{nome}-{uuid.uuid4().hex[:8]}")
    c.username_pw_set(cred["usuario"], cred["senha"])
    # certificado autoassinado da impressora: não validamos (igual à Bambu)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    c.tls_set_context(ctx)
    return c


class AnycubicPrinter:
    marca = "anycubic"
    recursos = ["camera", "temperaturas", "velocidade", "lote"]

    def __init__(self, cfg: dict):
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.ip = cfg["ip"]
        self.model = (cfg.get("model") or "KOBRA3").upper().replace(" ", "")
        self.serial = ""  # reencontrar por número de série é só nas Bambu
        self.access_code = ""
        self.camera_type = "flv"
        self.connected = False
        self.sem_conexao_desde = time.time()
        self.last_update = 0.0
        self.resposta_impressao = None
        self._cred: dict | None = None
        self._cli: mqtt.Client | None = None
        self._info: dict = {}
        self._temp: dict = {}
        self._proj: dict = {}
        self._luz: dict = {}
        self._caixas: dict = {}  # ACE / trocador de cores, por id
        self._video_url = None
        self._video = threading.Event()
        self._lock = threading.Lock()
        self._parar = threading.Event()

    # ---------- ciclo ----------

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._parar.set()
        self._desligar()

    def _desligar(self) -> None:
        c, self._cli = self._cli, None
        if c:
            try:
                c.loop_stop()
                c.disconnect()
            except Exception:
                pass

    def _loop(self) -> None:
        tentativa, consulta = 0.0, 0.0
        while not self._parar.is_set():
            agora = time.time()
            # sem conexão há 45 s: refaz o handshake (a senha do MQTT muda quando a impressora reinicia)
            if not self.connected and agora - tentativa > 45:
                tentativa = agora
                self._conectar()
            # o "info" (estado/trabalho) só chega sozinho de tempos em tempos: pedimos a cada 10 s
            if self.connected and agora - consulta > 10:
                consulta = agora
                self._consultar("info")
            self._parar.wait(1)

    def _conectar(self) -> None:
        self._desligar()
        try:
            cred = handshake(self.ip)
        except Exception as e:
            print(f"[{self.name}] Modo LAN: {e}")
            return
        self._cred = cred
        if cred["model_id"] in COM_LUZ:
            self.recursos = AnycubicPrinter.recursos + ["luz"]
        c = _cliente(cred, self.id)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        self._cli = c
        try:
            c.connect_async(self.ip, cred["porta"], keepalive=60)
            c.loop_start()
        except Exception as e:
            print(f"[{self.name}] falha ao conectar no MQTT: {e}")

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"[{self.name}] o MQTT da impressora recusou: {reason_code}")
            return
        cr = self._cred
        print(f"[{self.name}] conectada ({self.ip}, {cr['nome']})")
        self.connected = True
        self.sem_conexao_desde = None
        client.subscribe(f"{RAIZ}/printer/public/{cr['model_id']}/{cr['device_id']}/#")
        for tipo in ("info", "tempature", "light"):  # "tempature" é assim mesmo, errado no firmware
            self._consultar(tipo)
        self._publicar("multiColorBox", "getInfo", None)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if client is not self._cli:
            return
        if self.connected:
            print(f"[{self.name}] desconectada ({reason_code}), tentando de novo...")
            self.sem_conexao_desde = time.time()
        self.connected = False

    def _on_message(self, client, userdata, msg):
        try:
            obj = json.loads(msg.payload)
        except ValueError:
            return
        if not isinstance(obj, dict) or not msg.topic.endswith("/report"):
            return  # ".../response" só confirma o recebimento
        tipo = msg.topic.rsplit("/", 2)[-2]
        d = obj.get("data")
        with self._lock:
            self.last_update = time.time()
            if tipo == "info" and isinstance(d, dict):
                self._info = d
                self._temp = dict(d.get("temp") or {})
                self._proj = dict(d.get("project") or {})
            elif tipo == "tempature" and isinstance(d, dict):
                self._temp.update(d)
            elif tipo == "print":
                if isinstance(d, dict) and "progress" in d:
                    self._proj.update(d)  # progresso chega por aqui, a cada mudança
                if obj.get("action") == "start":
                    ok = obj.get("code") in (200, None) and obj.get("state") not in ("failed", "error")
                    self.resposta_impressao = {"result": "success" if ok else "failed",
                                               "reason": obj.get("msg") or obj.get("state")}
            elif tipo == "light" and isinstance(d, dict):
                luzes = d.get("lights")
                self._luz = (luzes[0] if luzes else {}) if isinstance(luzes, list) else d
            elif tipo == "multiColorBox" and isinstance(d, dict):
                for cx in d.get("multi_color_box") or []:
                    atual = self._caixas.setdefault(cx.get("id", 0), {})
                    slots = {s.get("index"): s for s in atual.get("slots") or []}
                    slots.update({s.get("index"): s for s in cx.get("slots") or []})
                    atual.update(cx, slots=sorted(slots.values(), key=lambda s: s.get("index") or 0))
            elif tipo == "video" and isinstance(d, dict):
                url = (d.get("urls") or {}).get("rtspUrl") or d.get("url")
                if url:
                    self._video_url = url
                self._video.set()

    # ---------- MQTT ----------

    def _publicar(self, tipo: str, acao: str, dados, espaco: str = "web") -> None:
        c, cr = self._cli, self._cred
        if not (c and cr and self.connected):
            raise ValueError("impressora desconectada")
        corpo = {"type": tipo, "action": acao, "timestamp": int(time.time() * 1000),
                 "msgid": str(uuid.uuid4()), "data": dados}
        c.publish(f"{RAIZ}/{espaco}/printer/{cr['model_id']}/{cr['device_id']}/{tipo}", json.dumps(corpo))

    def _consultar(self, tipo: str) -> None:
        try:
            self._publicar(tipo, "query", None)
        except ValueError:
            pass

    def _taskid(self) -> str:
        return str(self._proj.get("taskid") or "-1")

    # ---------- comandos (mesmos nomes do painel) ----------

    def command(self, action: str, value=None) -> None:
        if action in ("pause", "resume", "stop"):
            self._publicar("print", action, {"taskid": self._taskid()})
        elif action in ("set_nozzle", "set_bed"):
            chave = "target_nozzle_temp" if action == "set_nozzle" else "target_hotbed_temp"
            self._publicar("print", "update", {"taskid": self._taskid(), "settings": {chave: int(value)}})
        elif action == "set_speed":
            if int(value) not in VELOCIDADES:
                raise ValueError("velocidade deve ser 1 a 4")
            self._publicar("print", "update", {"taskid": self._taskid(),
                                               "settings": {"print_speed_mode": VELOCIDADES[int(value)]}})
        elif action in ("light_on", "light_off"):
            liga = action == "light_on"
            self._publicar("light", "control", {"type": 2, "status": int(liga), "brightness": 100 if liga else 0})
        elif action == "refresh":
            self._consultar("info")
        else:
            raise ValueError(f"ação não disponível na Anycubic: {action}")

    # ---------- câmera ----------

    def camera_url(self) -> str:
        url = self._video_url or (self._info.get("urls") or {}).get("rtspUrl")
        return _no_ip(self.ip, url, f"http://{self.ip}:18088/flv")

    def assistir_camera(self, ao_quadro, parar, intervalo: float = 0.8) -> None:
        """Liga a câmera (comando MQTT), lê o H.264 em HTTP-FLV e entrega ~1 JPEG por segundo."""
        import av  # o mesmo PyAV da câmera WebRTC das Creality
        self._video.clear()
        self._publicar("video", "startCapture", None)
        self._video.wait(5)  # Kobra 4 / X respondem com o endereço (com token) do vídeo
        try:
            with av.open(self.camera_url(), options={"rw_timeout": "10000000"}, timeout=10) as cont:
                ultimo = 0.0
                for quadro in cont.decode(video=0):
                    if parar():
                        break
                    if time.time() - ultimo < intervalo:
                        continue
                    ultimo = time.time()
                    buf = io.BytesIO()
                    quadro.to_image().save(buf, "JPEG", quality=75)
                    ao_quadro(buf.getvalue())
        finally:
            try:
                self._publicar("video", "stopCapture", None)
            except ValueError:
                pass

    # ---------- imprimir em lote ----------

    def _mapa_filamento(self) -> dict:
        """Com ACE / trocador de cores: usa o slot carregado (ou o primeiro com filamento).
        Sem nada: imprime com o filamento que estiver no bico."""
        for cx in self._caixas.values():
            slots = [s for s in cx.get("slots") or [] if s.get("type")]
            if not slots:
                continue
            s = next((s for s in slots if s.get("index") == cx.get("loaded_slot")), slots[0])
            cor = list(s.get("color") or [255, 255, 255])[:3]
            return {"use_ams": True, "ams_box_mapping": [{
                "paint_index": 0, "ams_index": s.get("index", 0), "paint_color": cor + [255],
                "ams_color": cor + [255], "material_type": s.get("type")}]}
        return {"use_ams": False, "ams_box_mapping": []}

    def imprimir_arquivo(self, caminho: str, nome: str, progresso=None) -> None:
        """Imprimir em lote: envia o .gcode pela porta 18910 e manda começar pelo MQTT."""
        from . import envio_http
        if not self.connected:
            raise ErroAnycubic("impressora desconectada")
        if not (self._info.get("urls") or {}).get("fileUploadurl"):
            self._consultar("info")  # o endereço de envio (com token) vem no relatório "info"
            fim = time.time() + 6
            while time.time() < fim and not (self._info.get("urls") or {}).get("fileUploadurl"):
                time.sleep(0.3)
        url = _no_ip(self.ip, (self._info.get("urls") or {}).get("fileUploadurl"),
                     f"http://{self.ip}:{PORTA_INFO}/gcode_upload")
        tamanho = os.path.getsize(caminho)
        resposta = envio_http.post_multipart(url, "gcode", caminho, nome, extras={"filename": nome},
                                             cabecalhos={"X-File-Length": str(tamanho)}, progresso=progresso)
        try:
            r = json.loads(resposta or "{}")
        except ValueError:
            r = {}
        if r.get("code") not in (200, None):
            raise ErroAnycubic(r.get("message") or r.get("msg") or "a impressora recusou o arquivo")
        remoto = ((r.get("data") or {}).get("gcode") if isinstance(r.get("data"), dict) else None) or nome
        md5 = hashlib.md5()
        with open(caminho, "rb") as f:
            while bloco := f.read(1 << 16):
                md5.update(bloco)
        self.resposta_impressao = None
        self._publicar("print", "start", {
            "taskid": "-1", "url": "", "filename": remoto, "md5": md5.hexdigest(), "filepath": None,
            "filetype": 1, "project_type": 1, "filesize": tamanho, "ams_settings": self._mapa_filamento(),
            "task_settings": {"auto_leveling": 1, "vibration_compensation": 0, "flow_calibration": 0,
                              "dry_mode": 0, "ai_settings": {"status": 0, "count": 0, "type": 0},
                              "timelapse": {"status": 0, "count": 0, "type": 0},
                              "drying_settings": {"status": 0, "target_temp": 0, "duration": 0, "remain_time": 0},
                              "model_objects_skip_parts": []}}, espaco="slicer")

    # ---------- estado no mesmo formato da Bambu ----------

    def summary(self) -> dict:
        with self._lock:
            info, t, proj, luz = self._info, dict(self._temp), dict(self._proj), self._luz
            caixas = list(self._caixas.values())
        if not info:
            estado = "UNKNOWN"
        else:
            estado = ESTADOS.get(proj.get("state"), "PREPARE" if info.get("state") == "busy" else "IDLE")
            if info.get("state") == "free" and estado in ("PREPARE", "RUNNING", "PAUSE"):
                estado = "IDLE"  # sobra de um trabalho que já acabou
            if estado == "IDLE" and info.get("state") == "free" and (info.get("last_project") or {}).get("state") == "finished":
                estado = "FINISH"
            if proj.get("pause") in (1, 2) and estado == "RUNNING":
                estado = "PAUSE"
        ativo = estado in ("RUNNING", "PAUSE", "PREPARE")
        restante = proj.get("remain_time")
        nivel = {1: 1, 2: 2, 3: 3}.get(info.get("print_speed_mode"))
        ace = [{"slot": s.get("index", i), "fonte": "ace", "tipo": s.get("type") or "",
                "cor": "".join(f"{int(x):02X}" for x in (s.get("color") or [])[:3])}
               for cx in caixas for i, s in enumerate(cx.get("slots") or []) if s.get("type")]
        return {
            "id": self.id, "name": self.name, "model": self.model, "marca": self.marca, "recursos": self.recursos,
            "connected": self.connected, "last_update": self.last_update,
            "status": estado, "job": (proj.get("filename") or "") if ativo or estado == "FINISH" else "",
            "progress": proj.get("progress") if ativo or estado == "FINISH" else None,
            "remaining_min": restante if ativo and isinstance(restante, (int, float)) else None,
            "layer": proj.get("curr_layer"), "total_layers": proj.get("total_layers"),
            "nozzle_temp": t.get("curr_nozzle_temp"), "nozzle_target": t.get("target_nozzle_temp"),
            "bed_temp": t.get("curr_hotbed_temp"), "bed_target": t.get("target_hotbed_temp"),
            "chamber_temp": t.get("curr_chamber_temp"), "speed_level": nivel,
            "error_code": 0 if estado != "FAILED" else 1,
            "light": bool(luz.get("status")) if luz else None,
            "sdcard": None, "arquivo": proj.get("filename") or "", "pulados": [],
            # o lote usa o slot carregado do ACE (ou o filamento do bico): ainda não escolhe slot
            "filamentos": [{"slot": 0, "fonte": "carregado", "tipo": "", "cor": ""}],
            "ace": ace,
        }


def testar(ip: str, espera: float = 8) -> str | None:
    """Handshake do Modo LAN + login no MQTT. Devolve o modelo da impressora (ou None)."""
    try:
        cred = handshake(ip, timeout=min(espera, 6))
    except Exception as e:
        print(f"teste Anycubic {ip}: {e}")
        return None
    ok = threading.Event()
    c = _cliente(cred, "teste")
    c.on_connect = lambda cl, u, f, rc, p: (not rc.is_failure) and ok.set()
    try:
        c.connect_async(ip, cred["porta"], keepalive=30)
        c.loop_start()
        ok.wait(espera)
    finally:
        threading.Thread(target=lambda: (c.loop_stop(), c.disconnect()), daemon=True).start()
    return cred["nome"] if ok.is_set() else None
