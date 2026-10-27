"""PrintBridge — controle local de impressoras 3D de várias marcas, numa interface só.

    import printbridge
    p = printbridge.criar({"id": "k1", "marca": "creality", "ip": "192.168.0.40"})
    p.start()
    print(p.summary())          # estado no mesmo formato para todas as marcas
    p.command("pause")

    printbridge.procurar()       # impressoras na rede local (Bambu, Creality, Klipper, Flashforge,
                                 # Anycubic, Elegoo, Prusa)

    g = printbridge.Gerenciador()   # guarda as impressoras em disco e as mantém conectadas
    python -m printbridge           # o painel web (e a API REST), em http://localhost:8750
"""

__version__ = "0.1.0"

from .descoberta import procurar
from .frota import MARCAS, criar, ficha, testar
from .gerenciador import Gerenciador

__all__ = ["MARCAS", "Gerenciador", "criar", "ficha", "procurar", "testar", "__version__"]
