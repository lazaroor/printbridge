"""Servidor web do PrintBridge: a interface (/), o modo embutido (/embed) e a API REST.

    python -m printbridge                 só este computador: http://localhost:8750
    python -m printbridge --rede          também a rede local (outros PCs, tablet, Raspberry Pi)

Quem pode o quê:
- A interface aberta NESTE computador (endereço localhost) usa a API sem token.
- Qualquer outro acesso (outro aparelho da rede, outro site, outro sistema) precisa do token:
  cabeçalho "Authorization: Bearer TOKEN" ou, onde não dá para mandar cabeçalho (iframe,
  <img>, EventSource), "?token=TOKEN" no endereço. O token aparece no terminal e em
  Configurações; dá para trocar a qualquer momento.
- Sites listados em "origens" podem embutir o /embed num iframe e chamar a API pelo navegador
  (CORS). Os outros não conseguem nem mostrar a página num iframe.
- Pedidos que mudam algo (POST/DELETE) exigem o cabeçalho X-PrintBridge: 1 ou o token — um site
  qualquer aberto no navegador não consegue mandar (bloqueia CSRF). O Host é conferido no modo
  local (bloqueia DNS rebinding). Nenhuma resposta traz access codes, senhas ou API keys.

API (JSON):
  GET    /api/info                              versão e marcas
  GET    /api/marcas                            fichas de cada marca (para montar um assistente)
  GET    /api/impressoras                       estado de todas
  GET    /api/impressoras/ID                    estado de uma
  GET    /api/eventos                           Server-Sent Events: a lista a cada 2 s
  GET    /api/impressoras/ID/camera.jpg         uma foto
  GET    /api/impressoras/ID/camera.mjpeg       vídeo (MJPEG, para <img>)
  GET    /api/impressoras/ID/pecas              Pular peças (Bambu): peças da impressão atual + prévia 3D
  POST   /api/impressoras                       {marca, campos}: testa e adiciona
  POST   /api/testar                            {marca, campos}: só testa
  POST   /api/descobrir                         {marca?}: procura na rede
  POST   /api/impressoras/ID/comando            {acao, valor}: pause, resume, stop, set_nozzle,
                                                set_bed, set_speed, light_on, light_off, skip ([ids])
  POST   /api/impressoras/ID/nome               {nome}
  POST   /api/impressoras/ID/imprimir?nome=X    corpo = o arquivo (.gcode / .gcode.3mf)
  DELETE /api/impressoras/ID
"""

import hmac
import ipaddress
import json
import sys
import re
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _ServidorExclusivo(ThreadingHTTPServer):
    """No Windows, SO_REUSEADDR (o padrão do http.server) deixa DOIS programas ouvirem a mesma
    porta — o segundo não percebe que ela está ocupada e os pedidos caem ora num, ora no outro.
    Sem ele, o bind falha e o servidor tenta a porta seguinte."""
    allow_reuse_address = sys.platform != "win32"
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import __version__, descoberta, marcas
from .frota import MARCAS
from .gerenciador import Gerenciador

PORTA_PADRAO = 8750
LIMITE_ARQUIVO = 512 * 1024 * 1024
WEB = Path(__file__).with_name("web")


class Servidor:
    def __init__(self, gerenciador: Gerenciador | None = None, porta: int = PORTA_PADRAO, rede: bool = False):
        self.g = gerenciador or Gerenciador()
        self.rede = rede
        self.envios: dict[str, dict] = {}  # impressora → {etapa, progresso, erro}
        host = "0.0.0.0" if rede else "127.0.0.1"
        self.http = None
        for p in range(porta, porta + 10):
            try:
                self.http = _ServidorExclusivo((host, p), self._handler())
                self.porta = p
                break
            except OSError:
                continue
        if not self.http:
            raise OSError(f"nenhuma porta livre entre {porta} e {porta + 9}")
        self.http.daemon_threads = True

    @property
    def url(self) -> str:
        return f"http://localhost:{self.porta}/"

    def iniciar(self, bloquear: bool = False) -> None:
        if bloquear:
            self.http.serve_forever()
        else:
            threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def parar(self) -> None:
        self.http.shutdown()
        self.g.parar()

    # ---------- envio de arquivo (em segundo plano, com progresso) ----------

    def _imprimir(self, pid: str, caminho: str, nome: str, slot) -> None:
        estado = self.envios[pid] = {"etapa": "enviando", "progresso": 0, "erro": None, "arquivo": nome}

        def progresso(feito, total):
            estado["progresso"] = round(100 * feito / max(1, total))

        try:
            self.g.imprimir(pid, caminho, nome, slot, progresso)
            estado.update(etapa="enviado", progresso=100)
        except Exception as e:
            estado.update(etapa="erro", erro=str(e) or type(e).__name__)
        finally:
            Path(caminho).unlink(missing_ok=True)

    def _handler(self):
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            # ----- quem está pedindo -----

            def _partes(self):
                u = urlsplit(self.path)
                return u.path.rstrip("/") or "/", {k: v[-1] for k, v in parse_qs(u.query).items()}

            def _local(self) -> bool:
                """A interface aberta neste computador (localhost, sem site de fora no meio)."""
                try:
                    loop = ipaddress.ip_address(self.client_address[0]).is_loopback
                except ValueError:
                    loop = False
                host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
                origem = self.headers.get("Origin")
                # só a própria página do PrintBridge: outro site em localhost (outra porta) não conta
                proprias = {f"http://{h}:{srv.porta}" for h in ("localhost", "127.0.0.1", "[::1]")}
                return loop and host in ("localhost", "127.0.0.1", "::1") and (origem is None or origem in proprias)

            def _token_ok(self, q: dict) -> bool:
                dado = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip() or q.get("token", "")
                return bool(dado) and hmac.compare_digest(dado, srv.g.cfg["token"])

            def _origem_permitida(self) -> str | None:
                o = (self.headers.get("Origin") or "").rstrip("/")
                return o if o and o in srv.g.cfg.get("origens", []) else None

            def _host_ok(self) -> bool:
                if srv.rede:
                    return True
                host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
                return host in ("localhost", "127.0.0.1", "::1")

            # ----- respostas -----

            def _cabecalhos_comuns(self):
                o = self._origem_permitida()
                if o:
                    self.send_header("Access-Control-Allow-Origin", o)
                    self.send_header("Vary", "Origin")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")

            def _json(self, codigo: int, obj) -> None:
                corpo = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(codigo)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(corpo)))
                self.send_header("Cache-Control", "no-store")
                self._cabecalhos_comuns()
                self.end_headers()
                self.wfile.write(corpo)

            def _erro(self, codigo: int, msg: str) -> None:
                self._json(codigo, {"erro": msg})

            def _pagina(self, arquivo: str, tipo: str, embutida: bool = False) -> None:
                try:
                    corpo = (WEB / arquivo).read_bytes()
                except OSError:
                    return self._erro(404, "não encontrado")
                self.send_response(200)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(corpo)))
                self.send_header("Cache-Control", "no-cache")  # atualizou o PrintBridge: a página nova já vale
                # só a própria página e as origens liberadas podem pôr o PrintBridge num iframe
                ancestrais = " ".join(["'self'"] + (srv.g.cfg.get("origens", []) if embutida else []))
                self.send_header("Content-Security-Policy", f"frame-ancestors {ancestrais}")
                self._cabecalhos_comuns()
                self.end_headers()
                self.wfile.write(corpo)

            def _ler_json(self) -> dict:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 64 * 1024:
                    raise ValueError("pedido grande demais")
                d = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(d, dict):
                    raise ValueError("pedido inválido")
                return d

            # ----- rotas -----

            def do_OPTIONS(self):
                self.send_response(204)
                o = self._origem_permitida()
                if o:
                    self.send_header("Access-Control-Allow-Origin", o)
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
                    self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-PrintBridge, X-Nome-Arquivo")
                    self.send_header("Access-Control-Max-Age", "600")
                    self.send_header("Vary", "Origin")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _autorizado(self, q: dict, mudando: bool) -> bool:
                if not self._host_ok():
                    return False
                if self._token_ok(q):
                    return True
                if not self._local():
                    return False
                return not mudando or self.headers.get("X-PrintBridge") == "1"

            def do_GET(self):
                caminho, q = self._partes()
                if caminho in ("/", "/index.html"):
                    return self._pagina("app.html", "text/html; charset=utf-8") if self._host_ok() else self._erro(403, "acesso negado")
                if caminho == "/embed":
                    return self._pagina("app.html", "text/html; charset=utf-8", embutida=True) if self._host_ok() else self._erro(403, "acesso negado")
                if caminho == "/printbridge.js":
                    return self._pagina("printbridge.js", "text/javascript; charset=utf-8")
                if caminho == "/mesa.js":  # prévia 3D do Pular peças (three.js embutido; gerado por npm run mesa)
                    return self._pagina("mesa.js", "text/javascript; charset=utf-8")
                if caminho in ("/icone.png", "/favicon.ico"):
                    return self._pagina("icone.png", "image/png")
                if not caminho.startswith("/api/"):
                    return self._erro(404, "não encontrado")
                if not self._autorizado(q, mudando=False):
                    return self._erro(401, "token inválido ou ausente")
                try:
                    return self._get_api(caminho, q)
                except KeyError:
                    return self._erro(404, "impressora não encontrada")

            def _get_api(self, caminho, q):
                if caminho == "/api/info":
                    return self._json(200, {"nome": "PrintBridge", "versao": __version__, "marcas": list(MARCAS),
                                            "local": self._local()})
                if caminho == "/api/marcas":
                    return self._json(200, marcas.publico())
                if caminho == "/api/impressoras":
                    return self._json(200, self._lista())
                if caminho == "/api/config":
                    if not self._local():
                        return self._erro(403, "só neste computador")
                    return self._json(200, {"token": srv.g.cfg["token"], "origens": srv.g.cfg.get("origens", []),
                                            "porta": srv.porta, "rede": srv.rede})
                if caminho == "/api/eventos":
                    return self._eventos()
                m = re.fullmatch(r"/api/impressoras/([0-9a-f]{8})(/camera\.jpg|/camera\.mjpeg|/pecas)?", caminho)
                if not m:
                    return self._erro(404, "não encontrado")
                pid, extra = m.groups()
                if not extra:
                    return self._json(200, self._com_envio(srv.g.resumo(pid)))
                if extra == "/pecas":
                    return self._json(200, srv.g.pecas(pid))
                if extra == "/camera.jpg":
                    jpg, _ = srv.g.foto(pid)
                    if not jpg:
                        return self._erro(503, "a câmera não mandou imagem (desligada, ocupada ou sem câmera)")
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(jpg)))
                    self.send_header("Cache-Control", "no-store")
                    self._cabecalhos_comuns()
                    self.end_headers()
                    return self.wfile.write(jpg)
                return self._mjpeg(pid)

            def _com_envio(self, s: dict) -> dict:
                e = srv.envios.get(s["id"])
                return {**s, "envio": e} if e else s

            def _lista(self) -> list:
                return [self._com_envio(s) for s in srv.g.lista()]

            def _eventos(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self._cabecalhos_comuns()
                self.end_headers()
                self.close_connection = True
                try:
                    while True:
                        dados = json.dumps(self._lista(), ensure_ascii=False)
                        self.wfile.write(f"event: impressoras\ndata: {dados}\n\n".encode())
                        self.wfile.flush()
                        time.sleep(2)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

            def _mjpeg(self, pid):
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=quadro")
                self.send_header("Cache-Control", "no-store")
                self._cabecalhos_comuns()
                self.end_headers()
                self.close_connection = True
                t = 0.0
                try:
                    while True:
                        jpg, t = srv.g.foto(pid, t, espera=15)
                        if not jpg:
                            break
                        self.wfile.write(b"--quadro\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpg) + jpg + b"\r\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

            def do_POST(self):
                caminho, q = self._partes()
                if not self._autorizado(q, mudando=True):
                    return self._erro(401, "token inválido ou ausente")
                try:
                    return self._post_api(caminho, q)
                except KeyError:
                    return self._erro(404, "impressora não encontrada")
                except ValueError as e:
                    return self._erro(400, str(e))
                except Exception as e:
                    print(f"PrintBridge: {caminho}: {e!r}")
                    return self._erro(500, f"algo deu errado ({type(e).__name__})")

            def _post_api(self, caminho, q):
                m = re.fullmatch(r"/api/impressoras/([0-9a-f]{8})/imprimir", caminho)
                if m:
                    return self._receber_arquivo(m.group(1), q)
                d = self._ler_json()
                if caminho == "/api/impressoras":
                    pid = srv.g.adicionar(str(d.get("marca") or ""), d.get("campos") or {})
                    return self._json(201, srv.g.resumo(pid))
                if caminho == "/api/testar":
                    ok = srv.g.testar(str(d.get("marca") or ""), d.get("campos") or {})
                    return self._json(200, {"ok": ok})
                if caminho == "/api/descobrir":
                    return self._json(200, {"achadas": self._descobrir(str(d.get("marca") or ""))})
                if caminho == "/api/config":
                    if not self._local():
                        return self._erro(403, "só neste computador")
                    if d.get("novo_token"):
                        srv.g.novo_token()
                    if "origens" in d:
                        srv.g.definir_origens(d.get("origens") or [])
                    return self._json(200, {"token": srv.g.cfg["token"], "origens": srv.g.cfg["origens"]})
                m = re.fullmatch(r"/api/impressoras/([0-9a-f]{8})/(comando|nome)", caminho)
                if not m:
                    return self._erro(404, "não encontrado")
                pid, oque = m.groups()
                if oque == "nome":
                    srv.g.renomear(pid, d.get("nome") or "")
                else:
                    srv.g.comando(pid, str(d.get("acao") or ""), d.get("valor"))
                return self._json(200, {"ok": True})

            def _descobrir(self, marca: str) -> list:
                ficha = marcas.POR_ID.get(marca) or {}
                alvo = ficha.get("adaptador") or marca or None
                cadastradas = srv.g.cfg["printers"]
                ja = {pc.get("serial") for pc in cadastradas if pc.get("serial")} | {pc["ip"] for pc in cadastradas}
                fora = []
                for x in descoberta.procurar(6):
                    if alvo and x.get("marca", "bambu") != alvo:
                        continue
                    if x["ip"] in ja or (x.get("marca") == "bambu" and x["serial"] in ja):
                        continue
                    fora.append({"marca": x.get("marca", "bambu"), "nome": x["name"], "modelo": x["model"], "ip": x["ip"],
                                 "serial": x["serial"] if x.get("marca", "bambu") == "bambu" else ""})
                return fora

            def _receber_arquivo(self, pid, q):
                srv.g.resumo(pid)  # 404 se não existe
                n = int(self.headers.get("Content-Length") or 0)
                if not 0 < n <= LIMITE_ARQUIVO:
                    raise ValueError("arquivo vazio ou maior que 512 MB")
                if (srv.envios.get(pid) or {}).get("etapa") == "enviando":
                    raise ValueError("já tem um arquivo indo para esta impressora")
                nome = q.get("nome") or self.headers.get("X-Nome-Arquivo") or "printbridge.gcode"
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix="-" + re.sub(r"[^\w.\-]", "_", nome)[-60:])
                with tmp:
                    falta = n
                    while falta:
                        bloco = self.rfile.read(min(falta, 1 << 20))
                        if not bloco:
                            break
                        tmp.write(bloco)
                        falta -= len(bloco)
                slot = int(q["slot"]) if q.get("slot", "").lstrip("-").isdigit() else None
                threading.Thread(target=srv._imprimir, args=(pid, tmp.name, nome, slot), daemon=True).start()
                return self._json(202, {"ok": True, "envio": "começou — acompanhe em envio.progresso"})

            def do_DELETE(self):
                caminho, q = self._partes()
                if not self._autorizado(q, mudando=True):
                    return self._erro(401, "token inválido ou ausente")
                m = re.fullmatch(r"/api/impressoras/([0-9a-f]{8})", caminho)
                if not m:
                    return self._erro(404, "não encontrado")
                srv.g.excluir(m.group(1))
                return self._json(200, {"ok": True})

        return H
