# PrintBridge-servidor.exe: só o servidor, com console e sem abrir o navegador (mini PC, servidor)
import sys

from printbridge.__main__ import main

if "--sem-navegador" not in sys.argv:
    sys.argv.append("--sem-navegador")
main()
