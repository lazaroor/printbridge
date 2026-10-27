"""Creality com o firmware de fábrica (K1, K1C, K1 Max, K1 SE, Ender-3 V3 / V3 KE / V3 Plus,
K2, Hi): a mesma conexão local que o Creality Print usa — WebSocket na porta 9999,
subprotocolo "wsslicer", mensagens JSON. Não precisa de root nem de conta na nuvem.

Protocolo seguido da integração da comunidade ha_creality_ws (Home Assistant):
- a impressora manda telemetria em JSON e, de tempos em tempos, {"ModeCode":"heart_beat"},
  que respondemos com o texto "ok";
- pedir estado: {"method":"get","params":{"ReqPrinterPara":1}};
- comandos: {"method":"set","params":{...}} — pause=1/0, stop=1, lightSw=1/0,
  nozzleTempControl=<°C>, bedTempControl={"num":0,"val":<°C>}, setFeedratePct=<%>.
Câmera: MJPEG em http://<ip>:8080/?action=stream (K1/K1C/K1 Max antigas e câmeras acessório)
ou WebRTC pela porta 8000 (K1C/K1 Max 1.3.5.22+, K1C 2025, K2, Hi) — webrtc_cam.py.
Imprimir arquivo (do projeto k2dash): POST http://<ip>/upload/<nome> (campo "file") e depois
{"method":"set","params":{"opGcodeFile":"printprt:<pasta>/<nome>","enableSelfTest":0}}.

NÃO TESTADO EM IMPRESSORA REAL — só contra um simulador do protocolo.
"""

import json
import threading
import time

import websocket  # pacote websocket-client

PORTA = 9999
VELOCIDADES = {1: 50, 2: 100, 3: 124, 4: 166}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class CrealityPrinter:
    marca = "creality"
    recursos = ["camera", "luz", "temperaturas", "velocidade", "lote"]

    def __init__(self, cfg: dict):
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.ip = cfg["ip"]
        self.model = (cfg.get("model") or "CREALITY").upper().replace(" ", "")
        self.serial = ""
        self.access_code = ""
        self.camera_modo = cfg.get("camera") or "auto"  # auto | mjpeg | webrtc
        self._falhas_camera = {"http": 0, "webrtc": 0}
        self.porta_upload = int(cfg.get("porta_upload") or 80)
        self.connected = False
        self.sem_conexao_desde = time.time()
        self.last_update = 0.0
        self.resposta_impressao = None
        self.state: dict = {}
        self._lock = threading.Lock()
        self._ws = None
        self._parar = threading.Event()

    # ---------- conexão ----------

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._pedir_estado, daemon=True).start()

    def stop(self) -> None:
        self._parar.set()
        try:
            self._ws and self._ws.close()
        except Exception:
            pass

    def _loop(self) -> None:
        while not self._parar.is_set():
            self._ws = websocket.WebSocketApp(
                f"ws://{self.ip}:{PORTA}", subprotocols=["wsslicer"],
                on_open=self._aberto, on_message=self._mensagem, on_close=self._fechado, on_error=lambda *_: None,
            )
            self._ws.run_forever(ping_interval=0)
            self._fechado()
            self._parar.wait(3)

    def _aberto(self, ws) -> None:
        self._enviar({"method": "get", "params": {"ReqPrinterPara": 1}})

    def _fechado(self, *_):
        if self.connected:
            print(f"[{self.name}] desconectada, tentando de novo...")
            self.sem_conexao_desde = time.time()
        self.connected = False

    def _mensagem(self, ws, texto) -> None:
        if isinstance(texto, bytes):
            texto = texto.decode("utf-8", "ignore")
        if texto == "ok":
            return
        try:
            d = json.loads(texto)
        except ValueError:
            return
        if not isinstance(d, dict):
            return
        if d.get("ModeCode") == "heart_beat":
            try:
                ws.send("ok")
            except Exception:
                pass
            return
        with self._lock:
            self.state.update(d)
            self.last_update = time.time()
        if not self.connected:
            print(f"[{self.name}] conectada ({self.ip}, Creality)")
        self.connected = True
        self.sem_conexao_desde = None

    def _pedir_estado(self) -> None:
        """Pede o estado completo de tempos em tempos (a impressora também empurra mudanças)."""
        while not self._parar.is_set():
            self._parar.wait(10)
            if self.connected:
                self._enviar({"method": "get", "params": {"ReqPrinterPara": 1}})

    def _enviar(self, obj: dict) -> None:
        if not self._ws:
            raise ConnectionError("sem conexão com a impressora")
        self._ws.send(json.dumps(obj, separators=(",", ":")))

    def _set(self, **params) -> None:
        self._enviar({"method": "set", "params": params})

    @property
    def camera_type(self) -> str:
        """MJPEG (porta 8080) nas K1 antigas; WebRTC (porta 8000) nas novas. Em "auto", decide
        pelo que a impressora informa (webrtcSupport), pelo modelo e por qual já falhou."""
        if self.camera_modo in ("mjpeg", "webrtc"):
            return "http" if self.camera_modo == "mjpeg" else "webrtc"
        with self._lock:
            suporte = str(self.state.get("webrtcSupport", ""))
        modelo = str(self.state.get("model") or self.model).upper().replace(" ", "")
        preferido = "webrtc" if suporte == "1" or modelo.startswith(("K2", "HI")) else "http"
        outro = "http" if preferido == "webrtc" else "webrtc"
        # o preferido falhou 2x seguidas e o outro ainda não: tenta o outro (K1C atualizada etc.)
        if self._falhas_camera[preferido] >= 2 and self._falhas_camera[outro] < 2:
            return outro
        return preferido

    def camera_falhou(self, tipo: str) -> None:
        self._falhas_camera[tipo] = self._falhas_camera.get(tipo, 0) + 1
        if all(v >= 2 for v in self._falhas_camera.values()):
            self._falhas_camera = {"http": 0, "webrtc": 0}  # recomeça o ciclo

    def camera_ok(self, tipo: str) -> None:
        self._falhas_camera[tipo] = 0

    def camera_url(self) -> str:
        if self.camera_type == "webrtc":
            return f"http://{self.ip}:8000/call/webrtc_local"
        return f"http://{self.ip}:8080/?action=stream"

    # ---------- imprimir um arquivo (Imprimir em lote) ----------

    def pasta_gcodes(self) -> str:
        """K1/Ender-3 V3: /usr/data; K2: /mnt/UDISK. O caminho do arquivo em impressão, quando
        a impressora informa, diz qual é."""
        atual = str(self.state.get("printFileName") or "")
        if "/printer_data/gcodes/" in atual:
            return atual.split("/printer_data/gcodes/")[0] + "/printer_data/gcodes"
        modelo = str(self.state.get("model") or self.model).upper()
        return "/mnt/UDISK/printer_data/gcodes" if modelo.startswith("K2") else "/usr/data/printer_data/gcodes"

    def imprimir_arquivo(self, caminho: str, nome: str, progresso=None) -> None:
        """Envia o G-code pelo serviço de upload da impressora (HTTP, porta 80) e manda imprimir."""
        from . import envio_http
        envio_http.post_multipart(f"http://{self.ip}:{self.porta_upload}/upload/{envio_http.quote(nome)}",
                                  "file", caminho, nome, progresso=progresso)
        self._set(opGcodeFile=f"printprt:{self.pasta_gcodes()}/{nome}", enableSelfTest=0)

    # ---------- comandos (mesmos nomes do painel) ----------

    def command(self, action: str, value=None) -> None:
        if action == "pause":
            self._set(pause=1)
        elif action == "resume":
            self._set(pause=0)
        elif action == "stop":
            self._set(stop=1)
        elif action in ("light_on", "light_off"):
            self._set(lightSw=1 if action == "light_on" else 0)
        elif action == "set_nozzle":
            self._set(nozzleTempControl=int(value))
        elif action == "set_bed":
            self._set(bedTempControl={"num": 0, "val": int(value)})
        elif action == "set_speed":
            if int(value) not in VELOCIDADES:
                raise ValueError("velocidade deve ser 1 a 4")
            self._set(setFeedratePct=VELOCIDADES[int(value)])
        elif action == "refresh":
            self._enviar({"method": "get", "params": {"ReqPrinterPara": 1}})
        else:
            raise ValueError(f"ação não disponível na Creality: {action}")

    # ---------- estado no mesmo formato da Bambu ----------

    def _status(self, s: dict) -> str:
        """Mesma lógica da integração de referência (derive_print_state)."""
        if not s:
            return "UNKNOWN"
        err = s.get("err")
        codigo = err.get("errcode") if isinstance(err, dict) else err
        if _num(codigo):
            return "FAILED"
        if not s.get("printFileName"):
            return "IDLE"
        progresso = _num(s.get("printProgress"))
        if progresso is None:
            progresso = _num(s.get("dProgress"))
        estado = _num(s.get("state"))
        if progresso is not None and progresso >= 100:
            return "FINISH"
        if estado == 5:
            return "PAUSE"
        if estado == 4:
            return "IDLE"  # parada pelo usuário
        if estado == 1:
            return "RUNNING"
        if estado in (0, 7):
            return "PREPARE"
        return "IDLE"

    def summary(self) -> dict:
        with self._lock:
            s = dict(self.state)
        status = self._status(s)
        progresso = _num(s.get("printProgress"))
        if progresso is None:
            progresso = _num(s.get("dProgress"))
        restante = _num(s.get("printLeftTime"))  # segundos
        modelo = str(s.get("model") or "").upper().replace(" ", "") or self.model
        err = s.get("err")
        codigo = err.get("errcode") if isinstance(err, dict) else err
        return {
            "id": self.id, "name": self.name, "model": modelo, "marca": self.marca, "recursos": self.recursos,
            "connected": self.connected, "last_update": self.last_update,
            "status": status, "job": s.get("printFileName") or "",
            "progress": round(progresso) if progresso is not None else None,
            "remaining_min": round(restante / 60) if restante is not None else None,
            "layer": s.get("layer"), "total_layers": s.get("TotalLayer"),
            "nozzle_temp": _num(s.get("nozzleTemp")), "nozzle_target": _num(s.get("targetNozzleTemp")),
            "bed_temp": _num(s.get("bedTemp0")), "bed_target": _num(s.get("targetBedTemp0")),
            "chamber_temp": _num(s.get("boxTemp")), "speed_level": None,
            "error_code": int(_num(codigo) or 0), "light": ("on" if str(s.get("lightSw")) == "1" else "off") if "lightSw" in s else None,
            "sdcard": None, "arquivo": s.get("printFileName") or "", "pulados": [],
            # sem AMS: no lote, a peça sai com o filamento que estiver carregado
            "filamentos": [{"slot": 0, "fonte": "carregado", "tipo": "", "cor": ""}],
        }


def testar(ip: str, espera: float = 8) -> str | None:
    """Conecta, espera a primeira telemetria e devolve o modelo informado (ou None)."""
    try:
        ws = websocket.create_connection(f"ws://{ip}:{PORTA}", subprotocols=["wsslicer"], timeout=espera)
    except Exception:
        return None
    try:
        ws.send(json.dumps({"method": "get", "params": {"ReqPrinterPara": 1}}))
        fim = time.time() + espera
        while time.time() < fim:
            texto = ws.recv()
            try:
                d = json.loads(texto)
            except (TypeError, ValueError):
                continue
            if isinstance(d, dict) and d.get("ModeCode") != "heart_beat":
                return str(d.get("model") or "Creality")
        return None
    except Exception:
        return None
    finally:
        try:
            ws.close()
        except Exception:
            pass
