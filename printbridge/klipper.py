"""Impressoras com Klipper + Moonraker (API HTTP na porta 7125).

Serve para: Anycubic Kobra com Rinkhals, Creality com root (K1/K1C/K1 Max/Ender-3 V3 com
Moonraker instalado), Creality Sonic Pad, Voron, Prusa com Klipper etc.

Mesma "cara" da BambuPrinter (summary/command/start/stop) para o resto do app não precisar
saber a marca. Lê o estado a cada 2 s (/printer/objects/query) e manda os comandos pela API
de impressão ou como G-code. Sem dependências externas.

NÃO TESTADO EM IMPRESSORA REAL — só contra um simulador da API do Moonraker.
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# velocidade do painel (igual à Bambu) → % de velocidade (M220)
VELOCIDADES = {1: 50, 2: 100, 3: 124, 4: 166}
ESTADOS = {"printing": "RUNNING", "paused": "PAUSE", "complete": "FINISH", "error": "FAILED",
           "cancelled": "IDLE", "standby": "IDLE"}
CONSULTA = "print_stats&display_status&virtual_sdcard&extruder&heater_bed&gcode_move&toolhead"


class MoonrakerPrinter:
    marca = "klipper"
    recursos = ["camera", "temperaturas", "velocidade", "lote"]

    def __init__(self, cfg: dict):
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.ip = cfg["ip"]
        self.porta = int(cfg.get("porta") or 7125)
        self.api_key = cfg.get("api_key") or ""
        self.luz = cfg.get("luz") or ""  # nome do output_pin/led da luz, se tiver (opcional)
        self.model = (cfg.get("model") or "KLIPPER").upper().replace(" ", "")
        self.serial = ""  # não usa: reencontrar por número de série é só nas Bambu
        self.access_code = ""
        self.camera_type = "http"
        self.connected = False
        self.sem_conexao_desde = time.time()
        self.last_update = 0.0
        self.resposta_impressao = None
        self._estado: dict = {}
        self._camera = None
        self._parar = threading.Event()
        if self.luz:
            self.recursos = self.recursos + ["luz"]

    # ---------- HTTP ----------

    def _url(self, caminho: str) -> str:
        return f"http://{self.ip}:{self.porta}{caminho}"

    def _req(self, caminho: str, metodo="GET", timeout=6):
        req = urllib.request.Request(self._url(caminho), method=metodo, data=b"" if metodo == "POST" else None,
                                     headers={"X-Api-Key": self.api_key} if self.api_key else {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")

    def gcode(self, linha: str) -> None:
        self._req("/printer/gcode/script?script=" + urllib.parse.quote(linha), "POST", timeout=15)

    # ---------- ciclo ----------

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._parar.set()

    def _loop(self) -> None:
        while not self._parar.is_set():
            try:
                d = self._req(f"/printer/objects/query?{CONSULTA}")
                self._estado = d.get("result", {}).get("status", {})
                self.last_update = time.time()
                if not self.connected:
                    print(f"[{self.name}] conectada ({self.ip}, Klipper)")
                self.connected = True
                self.sem_conexao_desde = None
            except Exception as e:
                if self.connected:
                    print(f"[{self.name}] desconectada ({e}), tentando de novo...")
                    self.sem_conexao_desde = time.time()
                self.connected = False
            self._parar.wait(2)

    def camera_url(self) -> str | None:
        """Primeira webcam cadastrada no Moonraker (crowsnest/mjpg-streamer)."""
        if self._camera is None:
            try:
                cams = self._req("/server/webcams/list").get("result", {}).get("webcams", [])
                url = next((c.get("stream_url") for c in cams if c.get("enabled", True) and c.get("stream_url")), None)
            except Exception:
                url = None
            url = url or "/webcam/?action=stream"
            self._camera = url if url.startswith("http") else f"http://{self.ip}{url if url.startswith('/') else '/' + url}"
        return self._camera

    def imprimir_arquivo(self, caminho: str, nome: str, progresso=None) -> None:
        """Imprimir em lote: envia o G-code para a pasta gcodes e já manda imprimir (print=true)."""
        from . import envio_http
        envio_http.post_multipart(self._url("/server/files/upload"), "file", caminho, nome,
                                  extras={"root": "gcodes", "print": "true"},
                                  cabecalhos={"X-Api-Key": self.api_key} if self.api_key else None, progresso=progresso)

    # ---------- comandos (mesmos nomes do painel) ----------

    def command(self, action: str, value=None) -> None:
        if action == "pause":
            self._req("/printer/print/pause", "POST")
        elif action == "resume":
            self._req("/printer/print/resume", "POST")
        elif action == "stop":
            self._req("/printer/print/cancel", "POST")
        elif action == "set_nozzle":
            self.gcode(f"M104 S{int(value)}")
        elif action == "set_bed":
            self.gcode(f"M140 S{int(value)}")
        elif action == "set_speed":
            if int(value) not in VELOCIDADES:
                raise ValueError("velocidade deve ser 1 a 4")
            self.gcode(f"M220 S{VELOCIDADES[int(value)]}")
        elif action in ("light_on", "light_off"):
            if not self.luz:
                raise ValueError("luz não configurada para esta impressora")
            self.gcode(f"SET_PIN PIN={self.luz} VALUE={1 if action == 'light_on' else 0}")
        elif action == "refresh":
            pass
        else:
            raise ValueError(f"ação não disponível no Klipper: {action}")

    # ---------- estado no mesmo formato da Bambu ----------

    def summary(self) -> dict:
        s = self._estado
        ps = s.get("print_stats", {}) or {}
        sd = s.get("virtual_sdcard", {}) or {}
        ext = s.get("extruder", {}) or {}
        mesa = s.get("heater_bed", {}) or {}
        info = ps.get("info") or {}
        estado = ESTADOS.get(ps.get("state"), "UNKNOWN" if not s else "IDLE")
        progresso = sd.get("progress")
        if progresso is None:
            progresso = (s.get("display_status") or {}).get("progress")
        pct = round(float(progresso) * 100) if isinstance(progresso, (int, float)) else None
        duracao = ps.get("print_duration") or 0
        restante = None
        if estado in ("RUNNING", "PAUSE") and progresso and progresso > 0.01:
            restante = max(0, round((duracao / progresso - duracao) / 60))
        fator = (s.get("gcode_move") or {}).get("speed_factor")
        nivel = None
        if isinstance(fator, (int, float)):
            nivel = min(VELOCIDADES, key=lambda k: abs(VELOCIDADES[k] - fator * 100))
        return {
            "id": self.id, "name": self.name, "model": self.model, "marca": self.marca, "recursos": self.recursos,
            "connected": self.connected, "last_update": self.last_update,
            "status": estado, "job": ps.get("filename") or "",
            "progress": pct, "remaining_min": restante,
            "layer": info.get("current_layer"), "total_layers": info.get("total_layer"),
            "nozzle_temp": ext.get("temperature"), "nozzle_target": ext.get("target"),
            "bed_temp": mesa.get("temperature"), "bed_target": mesa.get("target"),
            "chamber_temp": None, "speed_level": nivel,
            "error_code": 0 if estado != "FAILED" else 1, "light": None,
            "sdcard": None, "arquivo": ps.get("filename") or "", "pulados": [],
            # sem AMS: no lote, a peça sai com o filamento que estiver carregado
            "filamentos": [{"slot": 0, "fonte": "carregado", "tipo": "", "cor": ""}],
        }


def testar(ip: str, porta: int = 7125, api_key: str = "", espera: float = 6) -> str | None:
    """Confere se há um Moonraker respondendo. Devolve o nome da máquina (ou None)."""
    try:
        req = urllib.request.Request(f"http://{ip}:{int(porta or 7125)}/printer/info",
                                     headers={"X-Api-Key": api_key} if api_key else {})
        with urllib.request.urlopen(req, timeout=espera) as r:
            return (json.loads(r.read()).get("result") or {}).get("hostname") or "Klipper"
    except Exception:
        return None
