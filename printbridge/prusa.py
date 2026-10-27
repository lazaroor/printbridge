"""Prusa MK4 / MK4S / MK3.9 / MINI / MINI+ / XL / Core One pelo PrusaLink de fábrica (porta 80).

API HTTP local v1 do PrusaLink (firmware 4.7+/5.x/6.x), a mesma da página web da impressora:
- GET    /api/v1/status                 → estado, temperaturas, progresso, id do trabalho
- GET    /api/v1/job                    → nome do arquivo em impressão (204 se não houver)
- PUT    /api/v1/job/{id}/pause|resume  → pausar/continuar
- DELETE /api/v1/job/{id}               → cancelar
- PUT    /api/v1/files/usb/<nome>       → envia o G-code; com "Print-After-Upload: ?1" já imprime
Autenticação HTTP Digest: usuário "maker" e a senha da tela (Configurações → Rede → PrusaLink).
Firmwares antigos usam a mesma senha como X-Api-Key: tentamos Digest e, se recusar, a API key.

Mesma "cara" da BambuPrinter (summary/command/start/stop). A API não tem caminho documentado
para mudar temperatura nem velocidade, e a câmera da Prusa vai só para o Prusa Connect (nuvem):
por isso os recursos são só "lote".

NÃO TESTADO EM IMPRESSORA REAL — só contra um simulador escrito a partir da especificação
OpenAPI pública (github.com/prusa3d/Prusa-Link-Web, spec/openapi.yaml).
"""

import http.client
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PORTA = 80
USUARIO_PADRAO = "maker"
# velocidade do painel (igual à Bambu) → % de velocidade, só para mostrar (não dá para mudar)
VELOCIDADES = {1: 50, 2: 100, 3: 124, 4: 166}
ESTADOS = {"PRINTING": "RUNNING", "PAUSED": "PAUSE", "ATTENTION": "PAUSE", "BUSY": "PREPARE",
           "FINISHED": "FINISH", "ERROR": "FAILED", "STOPPED": "IDLE", "IDLE": "IDLE", "READY": "IDLE"}


class ErroPrusa(Exception):
    pass


class _Conexao:
    """Pedidos ao PrusaLink com Digest (urllib) e, se a impressora não aceitar, X-Api-Key."""

    def __init__(self, ip: str, usuario: str, senha: str):
        self.base = f"http://{ip}:{PORTA}"
        self.usuario = usuario or USUARIO_PADRAO
        self.senha = senha
        self.modo = "digest"  # vira "apikey" se só a API key funcionar
        senhas = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        senhas.add_password(None, self.base, self.usuario, senha)
        self.digest = urllib.request.HTTPDigestAuthHandler(senhas)
        self._abrir = urllib.request.build_opener(self.digest).open

    def pedir(self, caminho: str, metodo="GET", timeout=6) -> tuple[int, bytes]:
        for modo in ([self.modo] if self.modo == "apikey" else ["digest", "apikey"]):
            cab = {"Accept": "application/json"}
            if modo == "apikey":
                cab["X-Api-Key"] = self.senha
            req = urllib.request.Request(self.base + caminho, method=metodo, headers=cab)
            try:
                abrir = self._abrir if modo == "digest" else urllib.request.urlopen
                with abrir(req, timeout=timeout) as r:
                    self.modo = modo
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                if e.code != 401:
                    raise
                erro = e
            except ValueError as e:  # desafio "ApiKey": o handler Digest do urllib não sabe responder
                erro = ErroPrusa(str(e))
        raise erro

    def json(self, caminho: str, timeout=6) -> dict:
        codigo, corpo = self.pedir(caminho, timeout=timeout)
        return json.loads(corpo) if codigo == 200 and corpo else {}

    def autorizacao(self, metodo: str, caminho: str) -> dict:
        """Cabeçalho de autenticação já pronto, para o envio do arquivo (que vai em pedaços e
        não pode ser repetido): pega um desafio Digest novo e calcula a resposta com o urllib."""
        if self.modo == "apikey":
            return {"X-Api-Key": self.senha}
        try:
            urllib.request.urlopen(urllib.request.Request(self.base + "/api/version"), timeout=6).close()
            return {}  # sem senha nenhuma (não deveria acontecer no PrusaLink)
        except urllib.error.HTTPError as e:
            desafio = e.headers.get("WWW-Authenticate") or ""
        if not desafio.lower().startswith("digest "):
            return {"X-Api-Key": self.senha}
        chal = urllib.request.parse_keqv_list(urllib.request.parse_http_list(desafio[7:]))
        req = urllib.request.Request(self.base + caminho, method=metodo)
        return {"Authorization": "Digest " + self.digest.get_authorization(req, chal)}


class PrusaPrinter:
    marca = "prusa"
    recursos = ["lote"]

    def __init__(self, cfg: dict):
        self.id = cfg["id"]
        self.name = cfg.get("name", self.id)
        self.ip = cfg["ip"]
        self.model = (cfg.get("model") or "MK4").upper().replace(" ", "")
        self.serial = ""  # reencontrar por número de série é só nas Bambu
        self.access_code = ""
        self.camera_type = "nenhuma"  # a câmera da Prusa só fala com o Prusa Connect
        self.connected = False
        self.sem_conexao_desde = time.time()
        self.last_update = 0.0
        self.resposta_impressao = None
        self._con = _Conexao(self.ip, cfg.get("usuario") or USUARIO_PADRAO, cfg.get("senha") or "")
        self._estado: dict = {}
        self._job: dict = {}  # /api/v1/job do trabalho atual (para o nome do arquivo)
        self._parar = threading.Event()

    # ---------- ciclo ----------

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._parar.set()

    def _loop(self) -> None:
        while not self._parar.is_set():
            try:
                self._estado = self._con.json("/api/v1/status")
                job_id = (self._estado.get("job") or {}).get("id")
                if not job_id:
                    self._job = {}
                elif self._job.get("id") != job_id:
                    self._job = self._con.json("/api/v1/job")
                self.last_update = time.time()
                if not self.connected:
                    print(f"[{self.name}] conectada ({self.ip}, PrusaLink)")
                self.connected = True
                self.sem_conexao_desde = None
            except Exception as e:
                if self.connected:
                    print(f"[{self.name}] desconectada ({e}), tentando de novo...")
                    self.sem_conexao_desde = time.time()
                self.connected = False
            self._parar.wait(2)

    def camera_url(self) -> str | None:
        return None

    def imprimir_arquivo(self, caminho: str, nome: str, progresso=None) -> None:
        """Imprimir em lote: grava no pendrive (/usb) e já imprime (Print-After-Upload). Um
        .bgcode (G-code binário do PrusaSlicer) começa com "GCDE": aí o nome leva .bgcode."""
        with open(caminho, "rb") as f:
            binario = f.read(4) == b"GCDE"
        base = nome.rsplit(".", 1)[0] if nome.lower().endswith((".gcode", ".bgcode")) else nome
        destino = "/api/v1/files/usb/" + urllib.parse.quote(base + (".bgcode" if binario else ".gcode"))
        total = os.path.getsize(caminho)
        con = http.client.HTTPConnection(self.ip, PORTA, timeout=300)
        try:
            con.putrequest("PUT", destino)
            con.putheader("Content-Type", "application/octet-stream")
            con.putheader("Content-Length", str(total))
            con.putheader("Print-After-Upload", "?1")
            con.putheader("Overwrite", "?1")
            for k, v in self._con.autorizacao("PUT", destino).items():
                con.putheader(k, v)
            con.endheaders()
            enviados = 0
            with open(caminho, "rb") as f:
                while bloco := f.read(1 << 16):
                    con.send(bloco)
                    enviados += len(bloco)
                    if progresso:
                        progresso(enviados, total)
            r = con.getresponse()
            corpo = r.read().decode("utf-8", "replace")
            if r.status == 401:
                raise ErroPrusa("usuário/senha do PrusaLink recusados")
            if r.status == 409:
                raise ErroPrusa("a impressora está ocupada ou sem pendrive (409)")
            if r.status >= 300:
                raise ErroPrusa(f"a impressora respondeu {r.status} ao receber o arquivo: {corpo[:200]}")
        finally:
            con.close()

    # ---------- comandos (mesmos nomes do painel) ----------

    def command(self, action: str, value=None) -> None:
        if action == "refresh":
            return
        if action not in ("pause", "resume", "stop"):
            raise ValueError(f"ação não disponível na Prusa: {action}")
        job_id = (self._estado.get("job") or {}).get("id")
        if not job_id:
            raise ValueError("nenhuma impressão em andamento")
        caminho, metodo = (f"/api/v1/job/{job_id}", "DELETE") if action == "stop" else (f"/api/v1/job/{job_id}/{action}", "PUT")
        try:
            self._con.pedir(caminho, metodo)
        except urllib.error.HTTPError as e:
            if e.code in (404, 409):  # trabalho já acabou / estado não permite (ex.: pausar já pausada)
                raise ValueError(f"a impressora recusou agora ({e.code})") from e
            raise

    # ---------- estado no mesmo formato da Bambu ----------

    def summary(self) -> dict:
        s = self._estado
        pr = s.get("printer") or {}
        job = s.get("job") or {}
        estado = ESTADOS.get(pr.get("state"), "UNKNOWN" if not s else "IDLE")
        prog = job.get("progress")
        pct = round(float(prog)) if isinstance(prog, (int, float)) else None
        restante = job.get("time_remaining")
        restante = round(restante / 60) if isinstance(restante, (int, float)) and estado in ("RUNNING", "PAUSE") else None
        vel = pr.get("speed")
        nivel = min(VELOCIDADES, key=lambda k: abs(VELOCIDADES[k] - vel)) if isinstance(vel, (int, float)) else None
        arq = (self._job.get("file") or {}) if job.get("id") else {}
        arquivo = arq.get("display_name") or arq.get("name") or ""
        return {
            "id": self.id, "name": self.name, "model": self.model, "marca": self.marca, "recursos": self.recursos,
            "connected": self.connected, "last_update": self.last_update,
            "status": estado, "job": arquivo,
            "progress": pct, "remaining_min": restante,
            "layer": None, "total_layers": None,
            "nozzle_temp": pr.get("temp_nozzle"), "nozzle_target": pr.get("target_nozzle"),
            "bed_temp": pr.get("temp_bed"), "bed_target": pr.get("target_bed"),
            "chamber_temp": None, "speed_level": nivel,
            "error_code": 0 if estado != "FAILED" else 1, "light": None,
            "sdcard": None, "arquivo": arquivo, "pulados": [],
            # MMU/XL com várias cabeças ainda não mapeados: o lote sai com o filamento carregado
            "filamentos": [{"slot": 0, "fonte": "carregado", "tipo": "", "cor": ""}],
        }


def parece_prusalink(ip: str, espera: float = 2) -> bool:
    """Para a busca na rede: /api/version sem senha responde 401 com desafio Digest/ApiKey
    (PrusaLink de fábrica) ou 200 com "PrusaLink" no texto. Não precisa da senha."""
    try:
        with urllib.request.urlopen(f"http://{ip}:{PORTA}/api/version", timeout=espera) as r:
            return "prusalink" in str(json.loads(r.read() or b"{}").get("text", "")).lower()
    except urllib.error.HTTPError as e:
        desafio = (e.headers.get("WWW-Authenticate") or "").lower()
        return e.code == 401 and desafio.startswith(("digest ", "apikey "))
    except Exception:
        return False


def testar(ip: str, usuario: str = USUARIO_PADRAO, senha: str = "", espera: float = 6) -> str | None:
    """Confere IP + usuário + senha. Devolve o nome da impressora (ou None)."""
    try:
        con = _Conexao(ip, usuario, senha)
        if not con.json("/api/v1/status", timeout=espera).get("printer"):
            return None
        try:
            info = con.json("/api/v1/info", timeout=espera)
        except Exception:
            info = {}
        return info.get("name") or info.get("hostname") or "Prusa"
    except Exception:
        return None
