# PrintBridge

Local network control for 3D printers of many brands, behind one Python interface:
status, camera, pause/resume/stop, temperatures, speed, light, sending files to print,
skipping objects (Bambu Lab) and a 3D preview of each object from the G-code.

No cloud accounts. Everything talks to the printer directly on the local network, using the
printer's own protocol and firmware.

> *Português:* controle local de impressoras 3D de várias marcas numa interface só. O código
> e os comentários estão em português; a documentação de cada protocolo está em
> [PROTOCOLOS.md](PROTOCOLOS.md).

## Supported printers

| Brand | Models | What you need | Tested on real hardware |
|---|---|---|---|
| Bambu Lab | A1, A1 mini, P1P/P1S/P2S, X1C/X1E, H2D/H2S | IP, serial, access code (LAN Only + Developer Mode) | ✅ A1, A1 mini |
| Creality (stock firmware) | K1, K1C, K1 Max, K1 SE, Ender-3 V3 / KE / Plus, K2, Hi | IP only | ⚠️ simulator only |
| Klipper / Moonraker | QIDI, Sovol, Elegoo Neptune 4, Voron, rooted Creality, Sonic Pad, Kobra with Rinkhals | IP (+ API key if enabled) | ⚠️ simulator only |
| Flashforge | AD5X, Adventurer 5M / 5M Pro | IP, serial, access code ("LAN only" mode) | ⚠️ simulator only |
| Anycubic (stock firmware, LAN mode) | Kobra 3 / 3 Max, S1 / S1 Max, Kobra 4, Kobra X | IP only | ⚠️ simulator only |
| Elegoo | Centauri Carbon | IP only | ⚠️ simulator only |
| Prusa (PrusaLink) | MK4, MK4S, MK3.9, MINI, XL, Core One | IP, user (`maker`), password | ⚠️ simulator only |

"Simulator only" means the adapter was written from public protocol documentation and
community research and tested against a simulator of that protocol — **not yet on a real
printer**. Reports and fixes from owners are very welcome.

Which features each brand supports is listed in `printbridge/marcas.py` (`recursos`).

## Install

```bash
pip install -e .            # core
pip install -e ".[tudo]"    # + Anycubic (cryptography) and WebRTC/H.264 cameras (aiortc)
```

## Use

```python
import printbridge

for a in printbridge.procurar(6):          # discover printers on the local network
    print(a["marca"], a["ip"], a["name"])

p = printbridge.criar({"id": "k1", "marca": "creality", "ip": "192.168.0.40"})
p.start()
print(p.summary())                          # same dict shape for every brand
p.command("pause")                          # pause / resume / stop / set_nozzle / set_bed /
                                            # set_speed / light_on / light_off
```

`printbridge.ficha("anycubic")` returns what to ask the user and the step-by-step setup on
the printer's screen, so an app can build a setup wizard for every brand from one catalog.

See [examples/monitor.py](examples/monitor.py).

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE). Printer brand names are trademarks
of their owners; PrintBridge is independent and not affiliated with any manufacturer.
