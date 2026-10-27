"""Procura impressoras na rede local. Bambu Lab, de dois jeitos ao mesmo tempo (abaixo); e,
na varredura ativa, também Klipper/Moonraker (porta 7125) e Creality de fábrica (porta 9999).
Elegoo Centauri Carbon (SDCP): broadcast "M99999" na UDP 3000 e varredura da porta 3030.
Anycubic no Modo LAN de fábrica: varredura da porta 18910 (o /info diz o modelo).

Bambu Lab:

1. Escuta os anúncios SSDP (UDP 2021/1990) que as Bambu mandam a cada poucos segundos, com
   IP, número de série, modelo e nome. Precisa do Firewall do Windows liberar o app.
2. Varre a rede /24 procurando a porta 8883 e lê o número de série do certificado TLS.
   Só faz conexões de saída, então funciona mesmo com o Firewall bloqueando o app.

O access code NÃO vem em nenhum dos dois (é a "senha" da impressora): o usuário digita,
lendo da tela dela.
"""

import os
import socket
import ssl
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# Código de modelo do anúncio → nome usado no app. Códigos desconhecidos aparecem crus.
MODELOS = {
    "N2S": "A1", "N1": "A1MINI",
    "C11": "P1P", "C12": "P1S", "N7": "P2S",
    "BL-P001": "X1C", "BL-P002": "X1", "C13": "X1E",
    "O1D": "H2D", "O1S": "H2S",
}


def _parse(texto: str) -> dict:
    campos = {}
    for linha in texto.splitlines()[1:]:
        if ":" in linha:
            k, v = linha.split(":", 1)
            campos[k.strip().lower()] = v.strip()
    return campos


# Prefixo do número de série → modelo (para a busca ativa, que não recebe o anúncio)
PREFIXOS = {"039": "A1", "030": "A1MINI", "01P": "P1S", "01S": "P1P", "00M": "X1C", "00W": "X1", "03W": "X1E", "094": "H2D"}


def _serial_do_certificado(ip: str) -> str | None:
    """O certificado TLS da Bambu (porta 8883) traz o número de série no CN."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((ip, 8883), timeout=2) as raw, ctx.wrap_socket(raw) as s:
            der = s.getpeercert(binary_form=True)
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
            f.write(ssl.DER_cert_to_PEM_cert(der))
        try:
            sujeito = ssl._ssl._test_decode_cert(f.name)["subject"]
        finally:
            os.unlink(f.name)
        return dict(x[0] for x in sujeito).get("commonName")
    except Exception:
        return None


def _varrer(segundos: float) -> list[dict]:
    """Busca ativa: testa a porta 8883 em toda a rede /24 deste computador. Funciona mesmo
    com o Firewall do Windows bloqueando o app de RECEBER anúncios (só faz conexões de saída)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # não envia nada; só descobre o IP local da rota padrão
            local = s.getsockname()[0]
    except OSError:
        return []
    base = local.rsplit(".", 1)[0]
    abertos, lock = [], threading.Lock()  # (ip, porta)

    # 8883 = Bambu (MQTT) · 7125 = Klipper/Moonraker · 9999 = Creality (firmware de fábrica) · 8898 = Flashforge
    def testar(alvo):
        ip, porta = alvo
        try:
            with socket.create_connection((ip, porta), timeout=0.6):
                with lock:
                    abertos.append(alvo)
        except OSError:
            pass

    ips = [f"{base}.{i}" for i in range(1, 255) if f"{base}.{i}" != local]
    # Prusa (PrusaLink, porta 80) numa varredura à parte, ao mesmo tempo: muita coisa na rede tem
    # porta 80, e conferir /api/version em cada uma não pode atrasar as outras marcas
    prusas: list[dict] = []
    t_prusa = threading.Thread(target=lambda: prusas.extend(_varrer_prusa(ips)), daemon=True)
    t_prusa.start()
    with ThreadPoolExecutor(max_workers=96) as ex:
        ex.map(testar, [(ip, porta) for ip in ips for porta in (8883, 7125, 9999, 8898, 3030, 18910)], timeout=segundos)
    achadas = []
    for ip, porta in abertos:
        if porta == 8883:
            serial = _serial_do_certificado(ip)
            if serial:
                modelo = PREFIXOS.get(serial[:3], "")
                achadas.append({"marca": "bambu", "serial": serial, "ip": ip, "model": modelo, "name": f"Bambu {modelo or serial}".strip(), "modo": ""})
        elif porta == 7125:
            from . import klipper
            nome = klipper.testar(ip, espera=2)
            if nome:
                achadas.append({"marca": "klipper", "serial": f"klipper:{ip}", "ip": ip, "model": "KLIPPER", "name": nome, "modo": ""})
        elif porta == 8898:  # Flashforge (API do modo "somente LAN"); série e código a pessoa digita
            achadas.append({"marca": "flashforge", "serial": f"flashforge:{ip}", "ip": ip, "model": "", "name": f"Flashforge ({ip})", "modo": ""})
        elif porta == 3030:  # Elegoo Centauri Carbon (SDCP); confere falando o protocolo
            from . import elegoo
            modelo = elegoo.testar(ip, espera=2)
            if modelo:
                achadas.append({"marca": "elegoo", "serial": f"elegoo:{ip}", "ip": ip, "model": modelo.upper().replace(" ", ""), "name": f"{modelo} ({ip})", "modo": ""})
        elif porta == 18910:  # Anycubic (Modo LAN de fábrica): o /info diz o modelo; credenciais vêm no handshake
            from . import anycubic
            try:
                info = anycubic.ler_info(ip, 2)
            except Exception:
                continue
            nome = info.get("modelName") or "Anycubic"
            achadas.append({"marca": "anycubic", "serial": f"anycubic:{info.get('cn') or ip}", "ip": ip, "model": "",
                            "name": f"{nome} ({ip})" + (" — Modo LAN desligado" if info.get("ctrlType") == "cloud" else ""), "modo": ""})
        elif porta == 9999 and not any(a["ip"] == ip for a in achadas):
            achadas.append({"marca": "creality", "serial": f"creality:{ip}", "ip": ip, "model": "", "name": f"Creality ({ip})", "modo": ""})
    t_prusa.join(segundos)
    achadas += [a for a in list(prusas) if not any(x["ip"] == a["ip"] for x in achadas)]
    # uma Creality com root tem as duas portas: fica a Klipper (API mais completa)
    klippers = {a["ip"] for a in achadas if a["marca"] == "klipper"}
    return [a for a in achadas if not (a["marca"] == "creality" and a["ip"] in klippers)]


def _varrer_prusa(ips: list[str]) -> list[dict]:
    """Prusa com PrusaLink: porta 80 aberta e /api/version pedindo senha Digest (ou dizendo
    "PrusaLink"). Usuário e senha a pessoa digita, lendo da tela da impressora."""
    from . import prusa

    def testar(ip):
        try:
            with socket.create_connection((ip, 80), timeout=0.6):
                pass
        except OSError:
            return None
        if prusa.parece_prusalink(ip, espera=1.5):
            return {"marca": "prusa", "serial": f"prusa:{ip}", "ip": ip, "model": "", "name": f"Prusa ({ip})", "modo": ""}
        return None

    with ThreadPoolExecutor(max_workers=64) as ex:
        return [a for a in ex.map(testar, ips) if a]


def procurar(segundos: float = 6) -> list[dict]:
    """Junta as duas buscas: anúncios (trazem o nome dado na impressora) e varredura ativa."""
    varredura: list[dict] = []
    t = threading.Thread(target=lambda: varredura.extend(_varrer(segundos)), daemon=True)
    t.start()
    elegoos: list[dict] = []  # Elegoo SDCP: respondem ao "M99999" na UDP 3000, com o nome dado na impressora
    t_elegoo = threading.Thread(target=lambda: elegoos.extend(_elegoo_udp(segundos)), daemon=True)
    t_elegoo.start()
    anunciadas = _escutar(segundos)
    t.join()
    t_elegoo.join()
    anunciadas += elegoos
    por_serial = {a["serial"]: a for a in varredura}
    por_serial.update({a["serial"]: a for a in anunciadas})  # o anúncio tem nome e modelo melhores
    return list(por_serial.values())


def _elegoo_udp(segundos: float) -> list[dict]:
    try:
        from . import elegoo
        return elegoo.procurar(min(segundos, 3))
    except Exception:
        return []


def _escutar(segundos: float) -> list[dict]:
    """Escuta os anúncios por alguns segundos e devolve as impressoras encontradas."""
    achadas: dict[str, dict] = {}
    lock = threading.Lock()

    def escutar(porta: int):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", porta))
        except OSError:
            return
        s.settimeout(0.5)
        fim = time.time() + segundos
        while time.time() < fim:
            try:
                dados, origem = s.recvfrom(4096)
            except socket.timeout:
                continue
            c = _parse(dados.decode(errors="replace"))
            if "bambulab" not in c.get("nt", "") or not c.get("usn"):
                continue
            codigo = c.get("devmodel.bambu.com", "")
            with lock:
                achadas[c["usn"]] = {
                    "marca": "bambu",
                    "serial": c["usn"],
                    "ip": c.get("location") or origem[0],
                    "model": MODELOS.get(codigo, codigo),
                    "name": c.get("devname.bambu.com") or c["usn"],
                    "modo": c.get("devconnect.bambu.com", ""),  # "lan" = LAN Only ligado
                }
        s.close()

    threads = [threading.Thread(target=escutar, args=(p,), daemon=True) for p in (2021, 1990)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return list(achadas.values())
