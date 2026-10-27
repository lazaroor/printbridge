"""python -m printbridge [--porta 8750] [--rede] [--pasta DIR] — sobe a interface web."""

import argparse
import signal
import threading
import webbrowser

from .gerenciador import Gerenciador
from .servidor import PORTA_PADRAO, Servidor


def main() -> None:
    a = argparse.ArgumentParser(prog="printbridge", description="Interface web do PrintBridge")
    a.add_argument("--porta", type=int, default=PORTA_PADRAO)
    a.add_argument("--rede", action="store_true", help="aceitar acessos da rede local (sempre com token)")
    a.add_argument("--pasta", help="onde guardar a configuração (padrão: ~/.printbridge)")
    a.add_argument("--sem-navegador", action="store_true", help="não abrir o navegador ao iniciar")
    args = a.parse_args()

    g = Gerenciador(args.pasta)
    s = Servidor(g, args.porta, args.rede)
    s.iniciar()
    print(f"PrintBridge em {s.url}" + (f"  (rede: http://IP-DESTE-APARELHO:{s.porta}/)" if args.rede else ""))
    print(f"token da API (outros aparelhos e sistemas): {g.cfg['token']}")
    print(f"configuração em {g.arquivo}")
    if not args.sem_navegador:
        webbrowser.open(s.url)
    parar = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: parar.set())
    try:
        while not parar.wait(1):  # com timeout: no Windows o Ctrl+C só chega assim
            pass
    except KeyboardInterrupt:
        pass
    s.parar()


if __name__ == "__main__":
    main()
