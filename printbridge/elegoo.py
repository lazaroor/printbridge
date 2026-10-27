"""Elegoo Centauri Carbon com o firmware de fábrica: protocolo SDCP v3 na rede local, o mesmo
que o OrcaSlicer (ElegooLink) e o app da Elegoo usam. Não precisa de senha nem de conta.

Protocolo (documentação SDCP V3.0.0 da Chitu/CBD no GitHub, integração elegoo-homeassistant e
ElegooLink.cpp do OrcaSlicer):
- descoberta: texto "M99999" por broadcast UDP na porta 3000; a impressora responde com
  {"Id", "Data": {Name, MachineName, BrandName, MainboardIP, MainboardID, ...}};
- WebSocket ws://<ip>:3030/websocket, JSON. Pedido: {"Id", "Data": {"Cmd", "Data", "RequestID",
  "MainboardID", "TimeStamp", "From"}, "Topic": "sdcp/request/<MainboardID>"}; a impressora
  responde em sdcp/response/... (Data.Data.Ack) e empurra sdcp/status/... e sdcp/attributes/...;
  "ping" → "pong" mantém a conexão;
- Cmd 0 estado · 1 atributos · 128 imprimir {Filename:"/local/<nome>", StartLayer} · 129 pausar ·
  130 parar · 131 continuar · 386 vídeo {Enable} → VideoUrl · 403 controle: TempTargetNozzle,
  TempTargetHotbed, PrintSpeedPct (até 160), LightStatus {SecondLight};
- estado FDM: Status.CurrentStatus (lista; 1 = imprimindo), Status.PrintInfo.Status (tabela FDM:
  5/6 pausando/pausada, 9 concluída, 13 imprimindo, 14 erro…), Progress (0–100), CurrentTicks/
  TotalTicks em segundos, TempOfNozzle/TempOfHotbed/TempOfBox;
- câmera: MJPEG em http://<ip>:3031/video depois do Cmd 386 Enable=1;
- arquivo: POST http://<ip>:3030/uploadFile/upload em pedaços de 1 MB (multipart: S-File-MD5,
  Check, Offset, Uuid, TotalSize, File); resposta {"code":"000000"} = ok.

A Centauri Carbon 2 usa outro caminho (MQTT 1883, broker na própria impressora): não é esta.

NÃO TESTADO EM IMPRESSORA REAL — só contra um simulador escrito a partir dessas fontes.
"""

import hashlib
import http.client
import json
import os
import secrets
import socket
import threading
import time
import uuid

import websocket  # pacote websocket-client

PORTA = 3030
PORTA_VIDEO = 3031
PORTA_DESCOBERTA = 3000
PEDACO = 1 << 20  # o upload vai em pedaços de 1 MB, como no OrcaSlicer
# velocidade do painel (igual à Bambu) → % de velocidade; a Centauri vai de 50 (silencioso) a 160 (ludicrous)
VELOCIDADES = {1: 50, 2: 100, 3: 130, 4: 160}
# respostas do Cmd 128 (imprimir)
ACK_IMPRIMIR = {1: "a impressora está ocupada", 2: "a impressora não achou o arquivo",
                3: "o arquivo chegou corrompido (MD5)", 4: "a impressora não conseguiu ler o arquivo",
                5: "resolução incompatível", 6: "formato de arquivo desconhecido", 7: "o arquivo é de outro modelo"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class ElegooPrinter:
    marca = "elegoo"
    recursos = ["camera", "luz", "temperaturas", "velocidade", "lote"]

    def __init__(self, cfg: dict):
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.ip = cfg["ip"]
        self.model = (cfg.get("model") or "CENTAURICARBON").upper().replace(" ", "")
        self.serial = ""  # reencontrar por número de série é só nas Bambu
        self.access_code = ""
        self.camera_type = "http"
        self.connected = False
        self.sem_conexao_desde = time.time()
        self.last_update = 0.0
        self.resposta_impressao = None
        self.mainboard = ""  # MainboardID: vem na primeira mensagem da impressora
        self._status: dict = {}
        self._atributos: dict = {}
        self._video = ""
        self._esperando: dict[str, dict] = {}  # RequestID → {"evento", "resposta"}
        self._lock = threading.Lock()
        self._ws = None
        self._parar = threading.Event()

    # ---------- conexão ----------

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._manter, daemon=True).start()

    def stop(self) -> None:
        self._parar.set()
        try:
            self._ws and self._ws.close()
        except Exception:
            pass

    def _loop(self) -> None:
        while not self._parar.is_set():
            self._ws = websocket.WebSocketApp(
                f"ws://{self.ip}:{PORTA}/websocket",
                on_open=self._aberto, on_message=self._mensagem, on_close=self._fechado, on_error=lambda *_: None,
            )
            self._ws.run_forever(ping_interval=0)
            self._fechado()
            self._parar.wait(3)

    def _aberto(self, ws) -> None:
        self._cmd(1)  # atributos (nome, modelo, MainboardID)
        self._cmd(0)  # estado

    def _fechado(self, *_):
        if self.connected:
            print(f"[{self.name}] desconectada, tentando de novo...")
            self.sem_conexao_desde = time.time()
        self.connected = False

    def _manter(self) -> None:
        """"ping" a cada 10 s (a impressora responde "pong") e pede o estado a cada 5 s — ela
        também empurra mudanças sozinha, mas não garante a frequência."""
        n = 0
        while not self._parar.wait(5):
            if not self.connected:
                continue
            n += 1
            try:
                if n % 2 == 0:
                    self._ws.send("ping")
                self._cmd(0)
            except Exception:
                pass

    def _mensagem(self, ws, texto) -> None:
        if isinstance(texto, bytes):
            texto = texto.decode("utf-8", "ignore")
        if texto in ("pong", "ping"):
            return
        try:
            d = json.loads(texto)
        except ValueError:
            return
        if not isinstance(d, dict):
            return
        topico = str(d.get("Topic") or "")
        with self._lock:
            dados = d.get("Data") if isinstance(d.get("Data"), dict) else {}
            mb = d.get("MainboardID") or dados.get("MainboardID")
            if mb and not self.mainboard:
                self.mainboard = str(mb)
            if topico.startswith("sdcp/status/") or "Status" in d:
                st = d.get("Status") or {}
                if isinstance(st, dict):
                    self._status.update(st)
            elif topico.startswith("sdcp/attributes/") or "Attributes" in d:
                at = d.get("Attributes") or {}
                if isinstance(at, dict):
                    self._atributos.update(at)
            elif topico.startswith("sdcp/response/"):
                interno = dados.get("Data") or {}
                if dados.get("Cmd") == 386 and interno.get("VideoUrl"):
                    self._video = str(interno["VideoUrl"])
                espera = self._esperando.get(str(dados.get("RequestID") or ""))
                if espera:
                    espera["resposta"] = interno
                    espera["evento"].set()
            self.last_update = time.time()
        if not self.connected:
            print(f"[{self.name}] conectada ({self.ip}, Elegoo)")
        self.connected = True
        self.sem_conexao_desde = None

    def _cmd(self, cmd: int, dados: dict | None = None, esperar: float = 0) -> dict | None:
        """Manda um pedido SDCP. Com `esperar`, devolve o Data da resposta (ou erro se não vier)."""
        if not self._ws:
            raise ConnectionError("sem conexão com a impressora")
        rid = secrets.token_hex(16)
        pedido = {"Id": "", "Data": {"Cmd": cmd, "Data": dados or {}, "RequestID": rid, "MainboardID": self.mainboard,
                                     "TimeStamp": int(time.time()), "From": 0},
                  "Topic": f"sdcp/request/{self.mainboard}"}
        espera = {"evento": threading.Event(), "resposta": None}
        if esperar:
            self._esperando[rid] = espera
        try:
            self._ws.send(json.dumps(pedido, separators=(",", ":")))
            if not esperar:
                return None
            if not espera["evento"].wait(esperar):
                raise TimeoutError("a impressora não respondeu ao comando")
            return espera["resposta"] or {}
        finally:
            self._esperando.pop(rid, None)

    def _controle(self, **dados) -> None:
        r = self._cmd(403, dados, esperar=10)
        if r.get("Ack", 0) != 0:
            raise RuntimeError(f"a impressora recusou o comando (Ack {r.get('Ack')})")

    # ---------- câmera ----------

    def camera_url(self) -> str:
        """Liga o vídeo (Cmd 386) e usa o endereço que a impressora devolver; sem resposta,
        o padrão da Centauri Carbon (MJPEG na 3031)."""
        if self.connected:
            try:
                r = self._cmd(386, {"Enable": 1}, esperar=5)
                if r.get("Ack") == 1:
                    raise RuntimeError("a impressora já está no limite de vídeos abertos")
                if r.get("VideoUrl"):
                    self._video = str(r["VideoUrl"])
            except (TimeoutError, ConnectionError, OSError):
                pass
        url = self._video or f"http://{self.ip}:{PORTA_VIDEO}/video"
        return url if "://" in url else f"http://{url}"

    # ---------- imprimir um arquivo (Imprimir em lote) ----------

    def imprimir_arquivo(self, caminho: str, nome: str, progresso=None) -> None:
        """Envia o .gcode em pedaços de 1 MB para a memória da impressora e manda imprimir."""
        total = os.path.getsize(caminho)
        md5 = hashlib.md5()
        with open(caminho, "rb") as f:
            while bloco := f.read(1 << 16):
                md5.update(bloco)
        md5 = md5.hexdigest()
        ident = str(uuid.uuid4())
        enviados = 0
        with open(caminho, "rb") as f:
            while True:
                bloco = f.read(PEDACO)
                if not bloco and enviados:
                    break
                self._enviar_pedaco(nome, bloco, enviados, total, md5, ident)
                enviados += len(bloco)
                if progresso:
                    progresso(enviados, total)
                if enviados >= total:
                    break
        r = self._cmd(128, {"Filename": f"/local/{nome}", "StartLayer": 0}, esperar=30)
        ack = r.get("Ack", 0)
        self.resposta_impressao = r
        if ack != 0:
            raise RuntimeError(ACK_IMPRIMIR.get(ack, f"a impressora recusou (Ack {ack})"))

    def _enviar_pedaco(self, nome: str, bloco: bytes, offset: int, total: int, md5: str, ident: str) -> None:
        limite = "----printbridge" + secrets.token_hex(8)
        campos = {"S-File-MD5": md5, "Check": "1", "Offset": str(offset), "Uuid": ident, "TotalSize": str(total)}
        corpo = b"".join(f'--{limite}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                         for k, v in campos.items())
        corpo += (f'--{limite}\r\nContent-Disposition: form-data; name="File"; filename="{nome}"\r\n'
                  "Content-Type: application/octet-stream\r\n\r\n").encode() + bloco + f"\r\n--{limite}--\r\n".encode()
        con = http.client.HTTPConnection(self.ip, PORTA, timeout=120)
        try:
            con.request("POST", "/uploadFile/upload", body=corpo,
                        headers={"Content-Type": f"multipart/form-data; boundary={limite}"})
            r = con.getresponse()
            texto = r.read().decode("utf-8", "replace")
        finally:
            con.close()
        if r.status != 200:
            raise RuntimeError(f"a impressora respondeu {r.status} ao receber o arquivo: {texto[:200]}")
        try:
            d = json.loads(texto or "{}")
        except ValueError:
            raise RuntimeError(f"resposta inesperada ao receber o arquivo: {texto[:200]}")
        if str(d.get("code")) != "000000":
            msgs = "; ".join(f"{m.get('field')}: {m.get('message')}" for m in d.get("messages") or [] if isinstance(m, dict))
            raise RuntimeError(f"a impressora recusou o arquivo (código {d.get('code')}{', ' + msgs if msgs else ''})")

    # ---------- comandos (mesmos nomes do painel) ----------

    def command(self, action: str, value=None) -> None:
        if action in ("pause", "resume", "stop"):
            cmd = {"pause": 129, "resume": 131, "stop": 130}[action]
            r = self._cmd(cmd, esperar=10)
            if r.get("Ack", 0) != 0:
                raise RuntimeError(f"a impressora recusou o comando (Ack {r.get('Ack')})")
        elif action in ("light_on", "light_off"):
            luz = {"SecondLight": 1 if action == "light_on" else 0}
            with self._lock:
                rgb = (self._status.get("LightStatus") or {}).get("RgbLight")
            if rgb is not None:
                luz["RgbLight"] = rgb  # mantém a cor da fita de LED como está
            self._controle(LightStatus=luz)
        elif action == "set_nozzle":
            self._controle(TempTargetNozzle=max(0, min(320, int(value))))
        elif action == "set_bed":
            self._controle(TempTargetHotbed=max(0, min(110, int(value))))
        elif action == "set_speed":
            if int(value) not in VELOCIDADES:
                raise ValueError("velocidade deve ser 1 a 4")
            self._controle(PrintSpeedPct=VELOCIDADES[int(value)])
        elif action == "refresh":
            self._cmd(0)
        else:
            raise ValueError(f"ação não disponível na Elegoo: {action}")

    # ---------- estado no mesmo formato da Bambu ----------

    @staticmethod
    def _estado(s: dict) -> str:
        """CurrentStatus diz se há um trabalho (1 = imprimindo); o PrintInfo.Status diz a fase.
        O firmware fica parado em marcos ("aquecimento concluído"…) durante a impressão toda,
        então dentro de um trabalho o que não é pausa/erro/concluído conta como imprimindo."""
        if not s:
            return "UNKNOWN"
        atual = s.get("CurrentStatus")
        atual = set(atual) if isinstance(atual, list) else {atual}
        info = s.get("PrintInfo") or {}
        fase = info.get("Status")
        if fase == 14:
            return "FAILED"
        if 1 in atual:
            if fase in (5, 6):
                return "PAUSE"
            if fase == 9:
                return "FINISH"
            if fase in (7, 8):
                return "IDLE"
            if fase in (0, 1, 10, 11, 15, 16, 17) and not (_num(info.get("Progress")) or info.get("CurrentLayer")):
                return "PREPARE"
            return "RUNNING"
        if fase == 9 and info.get("Filename"):
            return "FINISH"  # a fase fica em "concluída" até o próximo trabalho
        return "IDLE"

    def summary(self) -> dict:
        with self._lock:
            s = dict(self._status)
            at = dict(self._atributos)
        estado = self._estado(s)
        info = s.get("PrintInfo") or {}
        progresso = _num(info.get("Progress"))
        if progresso is None and info.get("TotalLayer"):
            progresso = 100 * (_num(info.get("CurrentLayer")) or 0) / _num(info.get("TotalLayer"))
        restante = None
        atual, total = _num(info.get("CurrentTicks")), _num(info.get("TotalTicks"))  # segundos na FDM
        if estado in ("RUNNING", "PAUSE") and atual is not None and total:
            restante = max(0, round((total - atual) / 60))
        vel = _num(info.get("PrintSpeedPct"))
        nivel = min(VELOCIDADES, key=lambda k: abs(VELOCIDADES[k] - vel)) if vel is not None else None
        luz = (s.get("LightStatus") or {}).get("SecondLight")
        arquivo = str(info.get("Filename") or "")
        modelo = str(at.get("MachineName") or "").upper().replace(" ", "") or self.model
        return {
            "id": self.id, "name": self.name, "model": modelo, "marca": self.marca, "recursos": self.recursos,
            "connected": self.connected, "last_update": self.last_update,
            "status": estado, "job": arquivo,
            "progress": round(progresso) if progresso is not None and estado != "IDLE" else None,
            "remaining_min": restante,
            "layer": info.get("CurrentLayer"), "total_layers": info.get("TotalLayer"),
            "nozzle_temp": _num(s.get("TempOfNozzle")), "nozzle_target": _num(s.get("TempTargetNozzle")),
            "bed_temp": _num(s.get("TempOfHotbed")), "bed_target": _num(s.get("TempTargetHotbed")),
            "chamber_temp": _num(s.get("TempOfBox")), "speed_level": nivel,
            "error_code": int(_num(info.get("ErrorNumber")) or 0) or (1 if estado == "FAILED" else 0),
            "light": None if luz is None else ("on" if luz else "off"),
            "sdcard": None, "arquivo": arquivo, "pulados": [],
            # sem mapear o CANVAS (AMS da Elegoo): no lote, sai com o filamento carregado
            "filamentos": [{"slot": 0, "fonte": "carregado", "tipo": "", "cor": ""}],
        }


def testar(ip: str, espera: float = 8) -> str | None:
    """Conecta no WebSocket, pede os atributos e devolve o modelo informado (ou None)."""
    try:
        ws = websocket.create_connection(f"ws://{ip}:{PORTA}/websocket", timeout=espera)
    except Exception:
        return None
    try:
        ws.send(json.dumps({"Id": "", "Data": {"Cmd": 1, "Data": {}, "RequestID": secrets.token_hex(16), "MainboardID": "",
                                               "TimeStamp": int(time.time()), "From": 0}, "Topic": "sdcp/request/"}))
        fim = time.time() + espera
        while time.time() < fim:
            texto = ws.recv()
            try:
                d = json.loads(texto)
            except (TypeError, ValueError):
                continue
            if not isinstance(d, dict) or not str(d.get("Topic") or "").startswith("sdcp/"):
                continue
            at = d.get("Attributes") or {}
            if at or "Status" in d:
                return str(at.get("MachineName") or "Elegoo")
        return None
    except Exception:
        return None
    finally:
        try:
            ws.close()
        except Exception:
            pass


def procurar(segundos: float = 3, destino: str = "255.255.255.255") -> list[dict]:
    """Descoberta SDCP: "M99999" por broadcast na UDP 3000; cada impressora responde para nós.
    (`destino` só muda para testar contra um simulador neste computador.)"""
    achadas: dict[str, dict] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.5)
        try:
            s.sendto(b"M99999", (destino, PORTA_DESCOBERTA))
        except OSError:
            return []
        fim = time.time() + segundos
        while time.time() < fim:
            try:
                dados, origem = s.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                d = json.loads(dados.decode("utf-8", "replace"))
            except ValueError:
                continue
            at = d.get("Data") if isinstance(d, dict) and isinstance(d.get("Data"), dict) else {}
            at = at.get("Attributes", at)
            if not at.get("MainboardID") and not at.get("MachineName"):
                continue
            ip = at.get("MainboardIP") or origem[0]
            modelo = str(at.get("MachineName") or "").upper().replace(" ", "")
            achadas[ip] = {"marca": "elegoo", "serial": f"elegoo:{ip}", "ip": ip, "model": modelo,
                           "name": at.get("Name") or at.get("MachineName") or f"Elegoo ({ip})", "modo": ""}
    return list(achadas.values())
