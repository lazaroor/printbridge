"""Flashforge AD5X / Adventurer 5M / 5M Pro (firmware de fábrica, modo "somente LAN").

A impressora tem uma API HTTP na porta 8898 (a mesma que o Orca-Flashforge usa). Todo pedido
leva o número de série e o código de acesso que aparecem na tela do modo de rede:
- POST /detail      {serialNumber, checkCode}            → estado, temperaturas, câmera, AMS (IFS)
- POST /control     {serialNumber, checkCode, payload}   → pausar/continuar/cancelar, luz, velocidade
- POST /uploadGcode multipart (cabeçalhos serialNumber, checkCode, fileSize, printNow…) → imprime

Mesma "cara" da BambuPrinter (summary/command/start/stop). Temperatura não se muda por esta API
(só pelo G-code da porta 8899): por isso "temperaturas" fica fora dos recursos por enquanto.

NÃO TESTADO EM IMPRESSORA REAL — só contra um simulador escrito a partir da API pública.
"""

import base64
import json
import os
import threading
import time
import urllib.request

PORTA = 8898
# velocidade do painel (igual à Bambu) → % de velocidade
VELOCIDADES = {1: 50, 2: 100, 3: 124, 4: 166}
ESTADOS = {"printing": "RUNNING", "heating": "PREPARE", "calibrate_doing": "PREPARE", "busy": "PREPARE",
           "pausing": "PAUSE", "paused": "PAUSE", "completed": "FINISH", "error": "FAILED",
           "cancel": "IDLE", "ready": "IDLE"}


class ErroFlashforge(Exception):
    pass


def _post(ip: str, caminho: str, corpo: dict, timeout: float = 6) -> dict:
    req = urllib.request.Request(f"http://{ip}:{PORTA}{caminho}", data=json.dumps(corpo).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read() or b"{}")
    if d.get("code") != 0:  # código/série errados também voltam 200, com code != 0
        raise ErroFlashforge(d.get("message") or f"code {d.get('code')}")
    return d


class FlashforgePrinter:
    marca = "flashforge"
    recursos = ["camera", "luz", "velocidade", "lote"]

    def __init__(self, cfg: dict):
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.ip = cfg["ip"]
        self.serial_ff = (cfg.get("serial") or "").strip()
        self.codigo = (cfg.get("access_code") or "").strip()
        self.model = (cfg.get("model") or "AD5X").upper().replace(" ", "")
        self.serial = ""  # reencontrar por número de série é só nas Bambu
        self.access_code = ""
        self.camera_type = "http"
        self.connected = False
        self.sem_conexao_desde = time.time()
        self.last_update = 0.0
        self.resposta_impressao = None
        self._d: dict = {}
        self._parar = threading.Event()

    def _auth(self) -> dict:
        return {"serialNumber": self.serial_ff, "checkCode": self.codigo}

    def _controle(self, cmd: str, args: dict) -> None:
        _post(self.ip, "/control", {**self._auth(), "payload": {"cmd": cmd, "args": args}})

    # ---------- ciclo ----------

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._parar.set()

    def _loop(self) -> None:
        while not self._parar.is_set():
            try:
                self._d = _post(self.ip, "/detail", self._auth()).get("detail") or {}
                self.last_update = time.time()
                if not self.connected:
                    print(f"[{self.name}] conectada ({self.ip}, Flashforge)")
                self.connected = True
                self.sem_conexao_desde = None
            except Exception as e:
                if self.connected:
                    print(f"[{self.name}] desconectada ({e}), tentando de novo...")
                    self.sem_conexao_desde = time.time()
                self.connected = False
            self._parar.wait(2)

    def camera_url(self) -> str | None:
        return self._d.get("cameraStreamUrl") or f"http://{self.ip}:8080/?action=stream"

    def imprimir_arquivo(self, caminho: str, nome: str, progresso=None) -> None:
        """Imprimir em lote: envia o .gcode e já começa (printNow). Sem mapear o IFS: sai com o
        filamento do bico/slot ativo, como nas Creality."""
        from . import envio_http
        cab = {**self._auth(), "fileSize": str(os.path.getsize(caminho)), "printNow": "true",
               "levelingBeforePrint": "true", "flowCalibration": "false", "useMatlStation": "false",
               "gcodeToolCnt": "0", "materialMappings": base64.b64encode(b"[]").decode()}
        resposta = envio_http.post_multipart(f"http://{self.ip}:{PORTA}/uploadGcode", "gcodeFile", caminho, nome,
                                             cabecalhos=cab, progresso=progresso)
        try:
            if json.loads(resposta or "{}").get("code", 0) != 0:
                raise ErroFlashforge(json.loads(resposta).get("message") or "a impressora recusou o arquivo")
        except ValueError:
            pass

    # ---------- comandos (mesmos nomes do painel) ----------

    def command(self, action: str, value=None) -> None:
        if action in ("pause", "resume", "stop"):
            acao = {"pause": "pause", "resume": "continue", "stop": "cancel"}[action]
            self._controle("jobCtl_cmd", {"jobID": "", "action": acao})
        elif action in ("light_on", "light_off"):
            self._controle("lightControl_cmd", {"status": "open" if action == "light_on" else "close"})
        elif action == "set_speed":
            if int(value) not in VELOCIDADES:
                raise ValueError("velocidade deve ser 1 a 4")
            d = self._d
            self._controle("printerCtl_cmd", {"zAxisCompensation": d.get("zAxisCompensation") or 0,
                                              "speed": VELOCIDADES[int(value)],
                                              "chamberFan": d.get("chamberFanSpeed") or 0,
                                              "coolingFan": d.get("coolingFanSpeed") or 0, "coolingLeftFan": 0})
        elif action == "refresh":
            pass
        else:
            raise ValueError(f"ação não disponível na Flashforge: {action}")

    # ---------- estado no mesmo formato da Bambu ----------

    def summary(self) -> dict:
        d = self._d
        estado = ESTADOS.get(d.get("status"), "UNKNOWN" if not d else "IDLE")
        prog = d.get("printProgress")
        pct = round(float(prog) * 100) if isinstance(prog, (int, float)) else None
        restante = d.get("estimatedTime")
        restante = round(restante / 60) if isinstance(restante, (int, float)) and estado in ("RUNNING", "PAUSE") else None
        vel = d.get("printSpeedAdjust")
        nivel = min(VELOCIDADES, key=lambda k: abs(VELOCIDADES[k] - vel)) if isinstance(vel, (int, float)) else None
        # bico único: a temperatura vem como "right" (o "left" é do segundo bico, que essas não têm)
        bico, alvo = d.get("rightTemp", d.get("leftTemp")), d.get("rightTargetTemp", d.get("leftTargetTemp"))
        slots = ((d.get("matlStationInfo") or {}).get("slotInfos") or []) if d.get("hasMatlStation") else []
        filamentos = [{"slot": s.get("slotId", i + 1), "fonte": "ifs", "tipo": s.get("materialName") or "",
                       "cor": (s.get("materialColor") or "").lstrip("#")} for i, s in enumerate(slots) if s.get("hasFilament")]
        return {
            "id": self.id, "name": self.name, "model": self.model, "marca": self.marca, "recursos": self.recursos,
            "connected": self.connected, "last_update": self.last_update,
            "status": estado, "job": d.get("printFileName") or "",
            "progress": pct, "remaining_min": restante,
            "layer": d.get("printLayer"), "total_layers": d.get("targetPrintLayer"),
            "nozzle_temp": bico, "nozzle_target": alvo,
            "bed_temp": d.get("platTemp"), "bed_target": d.get("platTargetTemp"),
            "chamber_temp": d.get("chamberTemp"), "speed_level": nivel,
            "error_code": 0 if estado != "FAILED" else 1,
            "light": {"open": True, "close": False}.get(d.get("lightStatus")),
            "sdcard": None, "arquivo": d.get("printFileName") or "", "pulados": [],
            # o lote ainda não mapeia o IFS: imprime com o que estiver carregado
            "filamentos": [{"slot": 0, "fonte": "carregado", "tipo": "", "cor": ""}],
            "ifs": filamentos,
        }


def testar(ip: str, serial: str, codigo: str, espera: float = 6) -> str | None:
    """Confere IP + série + código. Devolve o nome da impressora (ou None)."""
    try:
        d = _post(ip, "/detail", {"serialNumber": serial, "checkCode": codigo}, timeout=espera).get("detail") or {}
        return d.get("name") or "Flashforge"
    except Exception:
        return None
