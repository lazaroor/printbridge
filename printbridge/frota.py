"""Criar e testar impressoras de qualquer marca a partir de uma configuração simples.

Cada impressora é um dicionário (o mesmo formato que um app guarda em disco):

    {"id": "a1", "marca": "bambu", "name": "A1 da bancada", "model": "A1", "ip": "192.168.0.23",
     "serial": "...", "access_code": "..."}               # Bambu Lab
    {"id": "k1", "marca": "creality", "ip": "..."}         # Creality de fábrica: só o IP
    {"id": "v0", "marca": "klipper", "ip": "...", "porta": 7125, "api_key": ""}
    {"id": "ad", "marca": "flashforge", "ip": "...", "serial": "...", "access_code": "..."}
    {"id": "ks", "marca": "anycubic", "ip": "..."}         # Modo LAN de fábrica: só o IP
    {"id": "cc", "marca": "elegoo", "ip": "..."}           # Centauri Carbon: só o IP
    {"id": "mk", "marca": "prusa", "ip": "...", "usuario": "maker", "senha": "..."}

Todas as impressoras têm a mesma interface: start(), stop(), summary() (estado no mesmo formato
para todas as marcas), command(acao, valor), e — quando a marca permite — camera_url() e
imprimir_arquivo(caminho, nome). A ficha de cada marca (o que pedir, o passo a passo na tela
da impressora, os recursos) está em marcas.py.
"""

from . import marcas

MARCAS = ("bambu", "creality", "klipper", "flashforge", "anycubic", "elegoo", "prusa")


def criar(cfg: dict):
    """O adaptador da marca (configuração sem "marca" é Bambu Lab)."""
    marca = cfg.get("marca") or "bambu"
    if marca == "bambu":
        from .bambu import BambuPrinter
        return BambuPrinter(cfg)
    if marca == "creality":
        from .creality import CrealityPrinter
        return CrealityPrinter(cfg)
    if marca == "klipper":
        from .klipper import MoonrakerPrinter
        return MoonrakerPrinter(cfg)
    if marca == "flashforge":
        from .flashforge import FlashforgePrinter
        return FlashforgePrinter(cfg)
    if marca == "anycubic":
        from .anycubic import AnycubicPrinter
        return AnycubicPrinter(cfg)
    if marca == "elegoo":
        from .elegoo import ElegooPrinter
        return ElegooPrinter(cfg)
    if marca == "prusa":
        from .prusa import PrusaPrinter
        return PrusaPrinter(cfg)
    raise ValueError(f"marca desconhecida: {marca} (conhecidas: {', '.join(MARCAS)})")


def testar(cfg: dict, espera: float = 10) -> bool:
    """Vê se a impressora responde com esses dados, sem guardar nada."""
    import threading
    import time

    marca = cfg.get("marca") or "bambu"
    ip = cfg["ip"]
    if marca == "creality":
        from . import creality
        return creality.testar(ip, espera) is not None
    if marca == "klipper":
        from . import klipper
        return klipper.testar(ip, int(cfg.get("porta") or 7125), cfg.get("api_key") or "", espera) is not None
    if marca == "flashforge":
        from . import flashforge
        return flashforge.testar(ip, cfg.get("serial") or "", cfg.get("access_code") or "", espera) is not None
    if marca == "anycubic":
        from . import anycubic
        return anycubic.testar(ip, espera) is not None
    if marca == "elegoo":
        from . import elegoo
        return elegoo.testar(ip, espera) is not None
    if marca == "prusa":
        from . import prusa
        return prusa.testar(ip, cfg.get("usuario") or "maker", cfg.get("senha") or "", espera) is not None
    from .bambu import BambuPrinter
    p = BambuPrinter({"id": "teste", "ip": ip, "serial": cfg.get("serial") or "",
                      "access_code": cfg.get("access_code") or "", "model": cfg.get("model") or "A1"})
    p.start()
    try:
        fim = time.time() + espera
        while time.time() < fim:
            if p.connected and p.last_update:
                return True
            time.sleep(0.3)
        return False
    finally:
        threading.Thread(target=p.stop, daemon=True).start()


def ficha(marca: str) -> dict:
    """O que pedir à pessoa e o passo a passo na impressora (para montar um assistente)."""
    return marcas.POR_ID[marca]
