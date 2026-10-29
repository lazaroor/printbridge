"""PrintBridge como aplicativo de computador: dois cliques e pronto.

Sobe o servidor (o mesmo de `python -m printbridge`), abre o painel no navegador e fica no
ícone ao lado do relógio, com "Abrir painel" e "Sair". Abrir de novo com ele já rodando só
abre o painel (não sobe um segundo servidor). É o que vira o PrintBridge.exe.

    python -m printbridge.aplicativo [--rede] [--porta 8750] [--minimizado]
"""

import argparse
import json
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from .gerenciador import Gerenciador, pasta_padrao
from .servidor import PORTA_PADRAO, Servidor


def _ja_rodando(porta: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://localhost:{porta}/api/info", timeout=1.5) as r:
            return json.loads(r.read()).get("nome") == "PrintBridge"
    except Exception:
        return False


def _registrar_saida(pasta: Path) -> None:
    """Sem console (o .exe), os avisos vão para printbridge.log, com data e hora."""
    if sys.stdout is not None and sys.stdout.isatty():
        return

    class Log:
        def __init__(self, arq):
            self.arq, self.inicio = arq, True

        def write(self, texto):
            for parte in texto.splitlines(keepends=True):
                if self.inicio:
                    self.arq.write(time.strftime("%d/%m %H:%M:%S "))
                self.arq.write(parte)
                self.inicio = parte.endswith("\n")
            return len(texto)

        def flush(self):
            self.arq.flush()

    try:
        pasta.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = Log(open(pasta / "printbridge.log", "a", buffering=1, encoding="utf-8"))
    except OSError:
        pass


def main() -> None:
    a = argparse.ArgumentParser(prog="PrintBridge")
    a.add_argument("--porta", type=int, default=PORTA_PADRAO)
    a.add_argument("--rede", action="store_true", help="aceitar acessos da rede local (sempre com token)")
    a.add_argument("--pasta", help="onde guardar a configuração (padrão: ~/.printbridge)")
    a.add_argument("--minimizado", action="store_true", help="não abrir o navegador (ao iniciar com o computador)")
    args = a.parse_args()

    pasta = Path(args.pasta) if args.pasta else pasta_padrao()
    _registrar_saida(pasta)
    if _ja_rodando(args.porta):  # já aberto: só mostra o painel
        webbrowser.open(f"http://localhost:{args.porta}/")
        return

    g = Gerenciador(pasta)
    s = Servidor(g, args.porta, args.rede)
    s.iniciar()
    print(f"PrintBridge em {s.url} (configuração em {g.arquivo})")
    if not args.minimizado:
        webbrowser.open(s.url)

    sair = threading.Event()
    try:
        import pystray
        from PIL import Image
    except ImportError:  # sem ícone na bandeja (ex.: Linux sem bandeja): fica rodando até Ctrl+C
        print("pystray não instalado: sem ícone na bandeja. Ctrl+C para sair.")
        try:
            while not sair.wait(1):
                pass
        except KeyboardInterrupt:
            pass
        s.parar()
        return

    imagem = Image.open(Path(__file__).with_name("web") / "icone.png")
    menu = pystray.Menu(
        pystray.MenuItem("Abrir painel", lambda *_: webbrowser.open(s.url), default=True),
        pystray.MenuItem("Pasta da configuração", lambda *_: webbrowser.open(g.pasta.as_uri())),
        pystray.MenuItem("Sair", lambda icone, *_: (sair.set(), icone.stop())),
    )
    icone = pystray.Icon("printbridge", imagem, f"PrintBridge — {s.url}", menu)
    icone.run()  # segura até "Sair"
    s.parar()


if __name__ == "__main__":
    main()
