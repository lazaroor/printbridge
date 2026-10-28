"""Gerenciador: guarda as impressoras em disco e as mantém conectadas.

É o que um app usa por baixo (a interface web do PrintBridge, ou um sistema seu):

    g = Gerenciador()                       # lê ~/.printbridge/config.json
    pid = g.adicionar("creality", {"ip": "192.168.0.40", "name": "K1C"})
    g.lista()                               # estado de todas, no mesmo formato
    g.comando(pid, "pause")
    g.foto(pid)                             # JPEG da câmera (liga sob demanda)
    g.imprimir(pid, "peca.gcode")           # envia e começa a imprimir

Os segredos (access code, senha, API key) ficam só no config.json — lista() nunca os devolve.
"""

import json
import os
import re
import secrets
import threading
import time
import zipfile
from pathlib import Path

from . import frota, marcas
from .camera import Camera

# o que cada marca guarda além de ip/nome/modelo (o resto do formulário é descartado)
EXTRAS = {
    "bambu": ("serial", "access_code"),
    "flashforge": ("serial", "access_code"),
    "klipper": ("porta", "api_key"),
    "prusa": ("usuario", "senha"),
}


def pasta_padrao() -> Path:
    if os.environ.get("PRINTBRIDGE_DIR"):
        return Path(os.environ["PRINTBRIDGE_DIR"])
    return Path.home() / ".printbridge"


class Gerenciador:
    def __init__(self, pasta: str | Path | None = None):
        self.pasta = Path(pasta) if pasta else pasta_padrao()
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.arquivo = self.pasta / "config.json"
        self.cfg = {"token": "", "origens": [], "printers": []}
        if self.arquivo.exists():
            try:
                self.cfg.update(json.loads(self.arquivo.read_text(encoding="utf-8")))
            except ValueError:
                pass
        if not self.cfg.get("token"):
            self.cfg["token"] = secrets.token_urlsafe(24)
            self._salvar()
        self.printers: dict = {}
        self.cameras: dict[str, Camera] = {}
        self._lock = threading.Lock()
        self._pecas: dict[str, dict] = {}       # impressora → peças da impressão atual (ou erro)
        self._lendo: set[str] = set()
        self._ultima_camada: dict[str, int] = {}
        for pc in self.cfg["printers"]:
            self._ligar(pc)

    # ---------- configuração ----------

    def _salvar(self) -> None:
        tmp = self.arquivo.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.arquivo)
        try:
            os.chmod(self.arquivo, 0o600)  # tem senhas: só o dono lê (no Linux/Raspberry Pi)
        except OSError:
            pass

    def novo_token(self) -> str:
        self.cfg["token"] = secrets.token_urlsafe(24)
        self._salvar()
        return self.cfg["token"]

    def definir_origens(self, origens: list[str]) -> list[str]:
        """Sites que podem embutir o PrintBridge (iframe) e chamar a API pelo navegador."""
        limpas = []
        for o in origens:
            o = str(o).strip().rstrip("/")
            if re.fullmatch(r"https?://[A-Za-z0-9.\-]+(:\d{1,5})?", o) and o not in limpas:
                limpas.append(o)
        self.cfg["origens"] = limpas[:20]
        self._salvar()
        return self.cfg["origens"]

    # ---------- impressoras ----------

    def _ligar(self, pc: dict) -> None:
        p = frota.criar(pc)
        with self._lock:
            self.printers[p.id] = p
            self.cameras[p.id] = Camera(p)
        p.start()

    def _validar(self, marca: str, campos: dict) -> tuple[dict, dict]:
        ficha = marcas.POR_ID.get(marca)
        if not ficha or ficha["situacao"] not in ("pronto", "parcial"):
            raise ValueError("esta marca ainda não está disponível")
        ids = [c["id"] for c in ficha["campos"]] + ["model"]
        v = {k: str(campos.get(k) or "").strip() for k in ids}
        for c in ficha["campos"]:
            if c.get("obrigatorio") and not v.get(c["id"]) and not c.get("padrao"):
                raise ValueError(f"preencha: {c['rotulo']}")
            if not v.get(c["id"]) and c.get("padrao"):
                v[c["id"]] = c["padrao"]
            if c.get("tamanho") and v.get(c["id"]) and len(v[c["id"]]) != c["tamanho"]:
                raise ValueError(f"{c['rotulo']} tem {c['tamanho']} caracteres")
        import ipaddress
        try:
            ipaddress.ip_address(v["ip"])
        except ValueError:
            raise ValueError("IP inválido (ex.: 192.168.0.23)")
        if v.get("porta") and not v["porta"].isdigit():
            raise ValueError("porta inválida")
        return ficha, v

    def _config_de(self, ficha: dict, v: dict) -> dict:
        marca = ficha["adaptador"]
        pc = {"marca": marca, "ip": v["ip"], "model": (v.get("model") or ficha["modelos"][0]).upper(),
              "name": v.get("name") or f"{ficha['nome']} {v['ip']}"}
        for k in EXTRAS.get(marca, ()):
            if v.get(k):
                pc[k] = int(v[k]) if k == "porta" else (v[k].upper() if k == "serial" and marca == "bambu" else v[k])
        return pc

    def testar(self, marca: str, campos: dict, espera: float = 10) -> bool:
        ficha, v = self._validar(marca, campos)
        return frota.testar(self._config_de(ficha, v), espera)

    def adicionar(self, marca: str, campos: dict, testar: bool = True) -> str:
        ficha, v = self._validar(marca, campos)
        pc = self._config_de(ficha, v)
        if testar and not frota.testar(pc):
            raise ValueError("a impressora não respondeu com esses dados — confira o passo a passo da marca")
        pc["id"] = secrets.token_hex(4)
        self.cfg["printers"].append(pc)
        self._salvar()
        self._ligar(pc)
        return pc["id"]

    def excluir(self, pid: str) -> None:
        with self._lock:
            p = self.printers.pop(pid, None)
            self.cameras.pop(pid, None)
        if p:
            threading.Thread(target=p.stop, daemon=True).start()
        self.cfg["printers"] = [x for x in self.cfg["printers"] if x["id"] != pid]
        self._salvar()

    def renomear(self, pid: str, nome: str) -> None:
        nome = str(nome).strip()[:60]
        pc = next((x for x in self.cfg["printers"] if x["id"] == pid), None)
        if not pc or not nome:
            raise ValueError("impressora ou nome inválido")
        pc["name"] = nome
        self._salvar()
        p = self.printers.get(pid)
        if p:
            p.name = nome

    def _impressora(self, pid: str):
        p = self.printers.get(pid)
        if not p:
            raise KeyError(pid)
        return p

    def resumo(self, pid: str) -> dict:
        p = self._impressora(pid)
        pc = next((x for x in self.cfg["printers"] if x["id"] == pid), {})
        s = p.summary()
        s.update(ip=pc.get("ip"), marca=pc.get("marca") or "bambu")
        return s

    def lista(self) -> list[dict]:
        with self._lock:
            ids = list(self.printers)
        lista = [self.resumo(pid) for pid in ids]
        for s in lista:
            self._esquecer_pecas_antigas(s)
        return lista

    def comando(self, pid: str, acao: str, valor=None) -> None:
        if acao == "skip":  # só peças que existem no arquivo em impressão
            conhecidas = {x["id"] for x in (self._pecas.get(pid) or {}).get("pecas") or []}
            valor = [int(v) for v in (valor or []) if int(v) in conhecidas]
            if not valor:
                raise ValueError("escolha peças da impressão atual (abra o Pular peças de novo)")
        self._impressora(pid).command(acao, valor)

    # ---------- pular peças (Bambu Lab) ----------

    def _esquecer_pecas_antigas(self, s: dict) -> None:
        """A lista de peças vale só para a impressão em que foi lida: reimprimir um arquivo de
        mesmo nome (e mesmo nº de camadas) não muda nada que dê para comparar. Ela some quando a
        impressão termina ou recomeça (a camada voltou)."""
        pid, camada = s["id"], s.get("layer")
        anterior = self._ultima_camada.get(pid)
        if s.get("status") in ("FINISH", "IDLE", "FAILED") or (
                camada is not None and anterior is not None and camada + 1 < anterior):
            self._pecas.pop(pid, None)
        if camada is not None:
            self._ultima_camada[pid] = camada

    def pecas(self, pid: str) -> dict:
        """Peças da impressão atual para o Pular peças, com a prévia 3D. A 1ª leitura baixa o
        arquivo do cartão (~1 min para 50 MB): enquanto isso, {"estado": "carregando"}."""
        p = self._impressora(pid)
        s = self.resumo(pid)
        self._esquecer_pecas_antigas(s)
        if "pular" not in (s.get("recursos") or []):
            return {"estado": "nao_suportado"}
        if s.get("status") not in ("RUNNING", "PAUSE", "PREPARE") or not s.get("arquivo"):
            return {"estado": "sem_impressao"}
        guardado = self._pecas.get(pid)
        if guardado and guardado.get("estado") == "erro" and time.time() - guardado.get("em", 0) > 90:
            self._pecas.pop(pid, None)  # erro (ex.: impressora ocupada no FTP): tenta de novo depois de 1,5 min
            guardado = None
        if guardado:
            return {**guardado, "pulados": s.get("pulados") or []}
        if pid not in self._lendo:
            self._lendo.add(pid)
            threading.Thread(target=self._ler_pecas, args=(p, s["arquivo"], s.get("total_layers")), daemon=True).start()
        return {"estado": "carregando"}

    def _ler_pecas(self, p, arquivo: str, camadas) -> None:
        from . import objetos
        try:
            d = objetos.ler(p.ip, p.access_code, arquivo, camadas)
            if camadas and d.get("camadas") and d["camadas"] != camadas:
                d = {"estado": "erro", "erro": f"o arquivo \"{d['arquivo']}\" no cartão é de outra placa ({d['camadas']} camadas; "
                                               f"a impressão tem {camadas}). Inicie a impressão de novo pelo fatiador."}
            else:
                d["estado"] = "pronto"
        except Exception as e:
            d = {"estado": "erro", "erro": f"não consegui ler as peças do arquivo ({e})"}
        d["em"] = time.time()
        self._pecas[p.id] = d
        self._lendo.discard(p.id)

    def foto(self, pid: str, depois: float = 0, espera: float = 10) -> tuple[bytes | None, float]:
        self._impressora(pid)
        cam = self.cameras[pid]
        cam.touch()
        return cam.wait_frame(depois, timeout=espera)

    # ---------- enviar e imprimir ----------

    def imprimir(self, pid: str, caminho: str, nome: str | None = None, slot: int | None = None, progresso=None) -> None:
        """Bambu Lab: .gcode.3mf (vai para o cartão SD e começa). Outras marcas: .gcode/.bgcode.
        slot (só Bambu): de onde sai o filamento — 0..3 AMS, 254 bobina externa; vazio = escolhe."""
        p = self._impressora(pid)
        nome = re.sub(r"[^\w.\- ]", "_", nome or Path(caminho).name)[:120]
        if getattr(p, "marca", "bambu") != "bambu":
            if not hasattr(p, "imprimir_arquivo"):
                raise ValueError("esta marca ainda não recebe arquivos")
            p.imprimir_arquivo(caminho, nome, progresso)
            return
        if not nome.lower().endswith(".3mf"):
            raise ValueError("na Bambu Lab, envie o .gcode.3mf exportado do Bambu Studio (Exportar arquivo fatiado)")
        with zipfile.ZipFile(caminho) as z:
            placas = sorted(int(m.group(1)) for n in z.namelist() if (m := re.match(r"Metadata/plate_(\d+)\.gcode$", n)))
            if not placas:
                raise ValueError("este .3mf não está fatiado — no Bambu Studio, use Exportar arquivo fatiado")
            info = z.read("Metadata/slice_info.config").decode("utf-8", "replace") if "Metadata/slice_info.config" in z.namelist() else ""
        n_fil = max(1, len(re.findall(r"<filament ", info)))
        if slot is None:
            carregados = [f for f in p.summary().get("filamentos") or [] if f.get("fonte") == "ams"]
            slot = int(carregados[0]["slot"]) if carregados else 254
        from . import envio
        envio.enviar(p.ip, p.access_code, caminho, nome, progresso)
        p.imprimir(nome, placas[0], [int(slot)] * n_fil, Path(nome).stem.replace(".gcode", ""))

    def parar(self) -> None:
        for p in list(self.printers.values()):
            p.stop()
