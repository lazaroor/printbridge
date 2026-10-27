"""Fatiamento com o Bambu Studio instalado neste computador, pela linha de comando.

- fatiar_projeto (EM USO): fatia uma placa de um projeto .3mf salvo no Bambu Studio, com as
  configurações do próprio projeto (impressora, perfil, filamentos, suporte, posição).
- fatiar (EM ESPERA): fatia STL com os perfis OFICIAIS da Bambu (resources\\profiles\\BBL).
  Testado (A1, A1 mini, cópias, suporte), mas ainda não ligado a nada — fica para os modelos
  personalizáveis do site, que geram STL. O Bambu Studio resolve a herança dos perfis
  ("inherits"), então passamos os oficiais direto; suporte = cópia do perfil de processo com
  o suporte ligado; várias cópias = o mesmo STL N vezes, organizado na mesa (--arrange).
"""

import json
import os
import subprocess
import zipfile
from functools import lru_cache
from pathlib import Path

CANDIDATOS = [
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Bambu Studio" / "bambu-studio.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Bambu Studio" / "bambu-studio.exe",
]

# modelo (como o app chama) → nome da máquina nos perfis oficiais (bico 0.4)
MAQUINAS = {
    "A1": "Bambu Lab A1", "A1MINI": "Bambu Lab A1 mini", "P1P": "Bambu Lab P1P", "P1S": "Bambu Lab P1S",
    "P2S": "Bambu Lab P2S", "X1": "Bambu Lab X1", "X1C": "Bambu Lab X1 Carbon", "X1E": "Bambu Lab X1E",
    "H2D": "Bambu Lab H2D", "H2S": "Bambu Lab H2S",
}

# o que a tela oferece → começo do nome do perfil de processo oficial
PERFIS = {
    "padrao": "0.20mm Standard", "qualidade": "0.16mm Optimal", "fino": "0.12mm Fine",
    "rapido": "0.24mm Draft", "resistente": "0.20mm Strength",
}


class ErroFatiamento(Exception):
    pass


def bambu_studio() -> Path | None:
    return next((c for c in CANDIDATOS if c.exists()), None)


def _pasta_perfis() -> Path:
    exe = bambu_studio()
    if not exe:
        raise ErroFatiamento("Bambu Studio não encontrado neste computador — instale-o para fatiar")
    return exe.parent / "resources" / "profiles" / "BBL"


@lru_cache(maxsize=1)
def _indice() -> dict:
    """{'process': [(nome, caminho, compatíveis)], 'filament': [...]} — só perfis instanciáveis."""
    base = _pasta_perfis()
    idx = {}
    for tipo in ("process", "filament"):
        lista = []
        for arq in (base / tipo).glob("*.json"):
            try:
                d = json.loads(arq.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if str(d.get("instantiation", "")).lower() == "true" and d.get("compatible_printers"):
                lista.append((d.get("name", arq.stem), arq, set(d["compatible_printers"])))
        idx[tipo] = lista
    return idx


def perfis_para(modelo: str, perfil: str, filamento: str) -> tuple[Path, Path, Path]:
    nome_maquina = MAQUINAS.get(modelo)
    if not nome_maquina:
        raise ErroFatiamento(f"ainda não sei fatiar para {modelo}")
    maquina_bico = f"{nome_maquina} 0.4 nozzle"
    maquina = _pasta_perfis() / "machine" / f"{maquina_bico}.json"
    if not maquina.exists():
        raise ErroFatiamento(f"perfil da impressora {maquina_bico} não está nesta versão do Bambu Studio")
    prefixo = PERFIS.get(perfil, PERFIS["padrao"])
    idx = _indice()
    processo = next((a for n, a, c in idx["process"] if n.startswith(prefixo) and maquina_bico in c), None)
    if not processo:
        raise ErroFatiamento(f"não achei o perfil {prefixo} para {nome_maquina}")
    fil = next((a for n, a, c in idx["filament"] if n.split(" @")[0] == f"Generic {filamento}" and maquina_bico in c), None)
    if not fil:
        raise ErroFatiamento(f"não achei perfil oficial de {filamento} para {nome_maquina}")
    return maquina, processo, fil


def _rodar(args: list[str], pasta: Path, saida: str) -> Path:
    """Roda o Bambu Studio e devolve pasta/saida; erros viram ErroFatiamento legível."""
    exe = bambu_studio()
    if not exe:
        raise ErroFatiamento("Bambu Studio não encontrado neste computador — instale-o para fatiar")
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / saida).unlink(missing_ok=True)
    (pasta / "result.json").unlink(missing_ok=True)
    try:
        subprocess.run([str(exe), *args, "--outputdir", str(pasta), "--export-3mf", saida], cwd=pasta,
                       capture_output=True, timeout=900, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise ErroFatiamento("o fatiamento passou de 15 minutos e foi cancelado")
    try:
        res = json.loads((pasta / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        res = {}
    if res.get("return_code", -1) != 0 or not (pasta / saida).exists():
        raise ErroFatiamento(f"o Bambu Studio não conseguiu fatiar: {res.get('error_string') or 'erro desconhecido'}")
    with zipfile.ZipFile(pasta / saida) as z:
        info = z.read("Metadata/slice_info.config").decode("utf-8", "replace")
    if 'key="outside" value="true"' in info:
        raise ErroFatiamento("há peças fora da mesa — confira a placa no Bambu Studio")
    return pasta / saida


def fatiar_projeto(projeto: Path, placa: int, pasta: Path) -> tuple[Path, int]:
    """Fatia só a placa escolhida do projeto. Devolve (arquivo .gcode.3mf, nº da placa nele)."""
    arq = _rodar(["--slice", str(int(placa)), str(projeto)], pasta, "fatiado.gcode.3mf")
    with zipfile.ZipFile(arq) as z:
        placas = sorted(int(n.split("_")[1].split(".")[0]) for n in z.namelist() if n.startswith("Metadata/plate_") and n.endswith(".gcode"))
    if not placas:
        raise ErroFatiamento("o Bambu Studio não gerou o G-code da placa")
    return arq, (int(placa) if int(placa) in placas else placas[0])


def fatiar(stl: Path, modelo: str, perfil: str, filamento: str, suporte: bool, copias: int, pasta: Path) -> Path:
    """EM ESPERA (STL dos personalizados). Gera pasta/<modelo>.gcode.3mf com uma placa."""
    maquina, processo, fil = perfis_para(modelo, perfil, filamento)
    pasta.mkdir(parents=True, exist_ok=True)
    if suporte:
        d = json.loads(processo.read_text(encoding="utf-8"))
        d.update({"enable_support": "1", "support_type": "tree(auto)", "name": f"{d.get('name')} + suporte", "from": "User"})
        processo = pasta / "processo-suporte.json"
        processo.write_text(json.dumps(d), encoding="utf-8")
    return _rodar(["--load-settings", f"{maquina};{processo}", "--load-filaments", str(fil),
                   "--arrange", "1", "--orient", "1", "--slice", "0", *[str(stl)] * max(1, min(int(copias), 30))],
                  pasta, f"{modelo}.gcode.3mf")
