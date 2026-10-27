"""Exemplo: procura impressoras na rede e mostra o estado de uma delas a cada 2 s.

    python examples/monitor.py                        # só procura
    python examples/monitor.py creality 192.168.0.40  # acompanha uma Creality (só o IP)
    python examples/monitor.py bambu 192.168.0.23 SERIAL ACCESS_CODE
"""

import sys
import time

import printbridge


def main() -> None:
    if len(sys.argv) < 3:
        print("procurando impressoras na rede (6 s)...")
        for a in printbridge.procurar(6):
            print(f"  {a['marca']:10} {a['ip']:15} {a['model'] or '':12} {a['name']}")
        return
    marca, ip, *resto = sys.argv[1:]
    cfg = {"id": "ex", "marca": marca, "ip": ip, "name": f"{marca} {ip}"}
    if marca in ("bambu", "flashforge") and len(resto) >= 2:
        cfg.update(serial=resto[0], access_code=resto[1])
    if marca == "prusa" and resto:
        cfg.update(usuario="maker", senha=resto[0])
    p = printbridge.criar(cfg)
    p.start()
    try:
        while True:
            s = p.summary()
            print(f"{time.strftime('%H:%M:%S')} conectada={s['connected']} {s['status']} "
                  f"{s['progress']}% bico={s['nozzle_temp']} mesa={s['bed_temp']}")
            time.sleep(2)
    except KeyboardInterrupt:
        p.stop()


if __name__ == "__main__":
    main()
