"""Peças (objetos) da impressão atual, para o "Pular peças".

A impressora só informa o NOME do arquivo que está imprimindo e a lista de peças já puladas
(s_obj). As peças em si estão no arquivo, no cartão SD: baixamos uma cópia (FTPS, só leitura)
e lemos do .gcode.3mf:
- slice_info.config → cada peça com identify_id (o número que o comando skip_objects usa) e
  nome; e se o fatiamento marcou os objetos (label_object_enabled) — sem isso não dá para pular;
- plate_N.json → retângulo de cada peça na mesa (mm);
- project_settings.config → tamanho da mesa;
- o próprio G-code → onde cada peça está de verdade e o contorno 3D (trajetos.py).

O cartão da Bambu entrega só ~0,1 MB/s durante a impressão e não aceita ler a partir do meio
(REST): um arquivo de 19 MB leva uns 3 minutos. Para não esperar tudo isso, o arquivo é lido
ENQUANTO chega: plate_N.json e as configurações vêm logo no começo, e a primeira camada do
G-code (onde todas as peças aparecem com o seu número) logo depois — com isso a lista e o 3D
das primeiras camadas saem em segundos (ao_parcial). O G-code vem de baixo para cima, então a
prévia 3D vai "crescendo" (novo ao_parcial a cada ~20 s) até o arquivo terminar, quando chegam
também os nomes oficiais.
Uma cópia do resultado fica em disco: reimprimir o mesmo arquivo não baixa de novo.
"""

import hashlib
import io
import json
import re
import struct
import threading
import time
import zipfile
import zlib
from pathlib import Path

from . import envio
from . import trajetos

_SEM_EXTENSAO = re.compile(r"\.(stl|3mf|obj|step)$", re.I)
_MOVIMENTO = re.compile(r"([XYE])(-?\d*\.?\d+)")


def _mesa(proj: dict) -> list[float]:
    """printable_area: ["0x0","256x0","256x256","0x256"] → [256, 256]"""
    try:
        pts = [tuple(float(v) for v in p.split("x")) for p in proj.get("printable_area") or []]
        return [max(x for x, _ in pts), max(y for _, y in pts)]
    except (ValueError, TypeError):
        return [256.0, 256.0]


def _caixas_por_posicao(centros: dict[int, tuple[float, float]], caixas: list[dict]) -> dict[int, dict]:
    """Casa cada peça (pelo centro dela na mesa) com o retângulo do plate_N.json que a contém —
    os ids do plate_N.json são outros, e casar pelo nome erra quando há peças de mesmo nome."""
    fora = {}
    for pid, (cx, cy) in centros.items():
        dentro = [c for c in caixas if len(c.get("bbox") or []) >= 4
                  and c["bbox"][0] - 1 <= cx <= c["bbox"][2] + 1 and c["bbox"][1] - 1 <= cy <= c["bbox"][3] + 1]
        if not dentro and caixas:  # nenhum contém: o de centro mais próximo
            dentro = [min(caixas, key=lambda c: ((c["bbox"][0] + c["bbox"][2]) / 2 - cx) ** 2 + ((c["bbox"][1] + c["bbox"][3]) / 2 - cy) ** 2)]
        if dentro:
            fora[pid] = min(dentro, key=lambda c: (c["bbox"][2] - c["bbox"][0]) * (c["bbox"][3] - c["bbox"][1]))
    return fora


class _PrimeiraCamada:
    """Lê o começo do G-code (texto) até o fim da primeira camada: nº total de camadas e, de cada
    peça, alguns pontos do contorno — para saber onde ela está na mesa."""

    def __init__(self):
        self.camadas = None
        self.pontos: dict[int, list[tuple[float, float]]] = {}
        self.ordem: list[int] = []
        self.pronta = False
        self._resto = b""
        self._camada = 0
        self._peca = None
        self._x = self._y = 0.0
        self._lido = 0

    def alimentar(self, texto: bytes) -> None:
        if self.pronta:
            return
        self._lido += len(texto)
        linhas = (self._resto + texto).split(b"\n")
        self._resto = linhas.pop()
        for bruta in linhas:
            if bruta.startswith(b";"):
                if bruta.startswith(b"; CHANGE_LAYER"):
                    self._camada += 1
                    if self._camada >= 2:  # começou a 2ª camada: a 1ª já mostrou todas as peças
                        self.pronta = True
                        return
                elif bruta.startswith(b"; total layer number:"):
                    self.camadas = int(bruta.rsplit(b":", 1)[1])
                elif bruta.startswith(b"; start printing object, unique label id:"):
                    self._peca = int(bruta.rsplit(b":", 1)[1])
                    if self._peca not in self.pontos:
                        self.pontos[self._peca] = []
                        self.ordem.append(self._peca)
                elif bruta.startswith(b"; stop printing object"):
                    self._peca = None
                continue
            if not bruta.startswith((b"G1 ", b"G0 ", b"G2 ", b"G3 ")):
                continue
            v = dict(_MOVIMENTO.findall(bruta.decode("ascii", "ignore")))
            self._x = float(v.get("X", self._x))
            self._y = float(v.get("Y", self._y))
            if self._peca is not None and float(v.get("E", 0)) > 0 and len(self.pontos[self._peca]) < 400:
                self.pontos[self._peca].append((self._x, self._y))
        if self._lido > 40_000_000:  # algo estranho (sem CHANGE_LAYER): desiste da leitura rápida
            self.pronta = True


class _LeitorEmAndamento:
    """Percorre o .zip (.3mf) enquanto ele chega, entrada por entrada. Os arquivos da Bambu não
    trazem os tamanhos no cabeçalho de cada entrada (vêm num descritor depois dos dados): as
    compactadas acabam quando o zlib diz que acabou; as guardadas sem compressão (imagens), no
    descritor "PK\\x07\\x08" cujo tamanho bate."""

    def __init__(self, camadas_impressao):
        self.camadas_impressao = camadas_impressao
        self.buf = bytearray()
        self.pos = 0
        self.entrada = None
        self.pequenos: dict[str, bytes] = {}
        self.placa = None
        self.camada1: _PrimeiraCamada | None = None
        self.gcode: bytearray | None = None  # G-code da placa em impressão, até onde já chegou
        self.parado = False

    def alimentar(self, bloco: bytes) -> None:
        self.buf += bloco
        if not self.parado:
            try:
                self._andar()
            except Exception:  # formato inesperado: fica só a leitura completa, no fim
                self.parado = True

    def _andar(self) -> None:
        while not self.parado:
            if self.entrada is None:
                if len(self.buf) < self.pos + 30:
                    return
                if self.buf[self.pos:self.pos + 4] != b"PK\x03\x04":  # diretório central: acabaram as entradas
                    self.parado = True
                    return
                _, _, flag, metodo, _, _, _, comp, _, nl, el = struct.unpack("<IHHHHHIIIHH", self.buf[self.pos:self.pos + 30])
                if len(self.buf) < self.pos + 30 + nl + el:
                    return
                nome = self.buf[self.pos + 30:self.pos + 30 + nl].decode("utf-8", "replace")
                inicio = self.pos + 30 + nl + el
                self.entrada = {"nome": nome, "metodo": metodo, "inicio": inicio, "alimentado": inicio,
                                "tam": comp if not flag & 8 else None, "dados": bytearray(),
                                "z": zlib.decompressobj(-15) if metodo == 8 else None}
                m = re.fullmatch(r"Metadata/plate_(\d+)\.gcode", nome)
                if m and self.camada1 is None:
                    self.entrada["gcode"] = int(m.group(1))
                    self.entrada["camada"] = _PrimeiraCamada()
                    self.entrada["texto"] = bytearray()  # o G-code até aqui: vira o 3D parcial
            e = self.entrada
            if e["metodo"] == 8:
                novo = bytes(self.buf[e["alimentado"]:])
                e["alimentado"] = len(self.buf)
                saida = e["z"].decompress(novo)
                self._usar(e, saida)
                if not e["z"].eof:
                    return
                fim = e["alimentado"] - len(e["z"].unused_data)
            else:  # guardado: o fim é o descritor cujo tamanho bate
                fim = None
                busca = e.get("busca", e["inicio"])
                while True:
                    i = self.buf.find(b"PK\x07\x08", busca)
                    if i < 0 or i + 16 > len(self.buf):
                        e["busca"] = max(e["inicio"], len(self.buf) - 16)
                        return
                    if struct.unpack("<I", self.buf[i + 8:i + 12])[0] == i - e["inicio"]:
                        fim = i
                        break
                    busca = i + 1
            self._fechar(e, fim)
            if self.parado:
                return

    def _usar(self, e: dict, saida: bytes) -> None:
        if re.fullmatch(r"Metadata/(plate_\d+\.json|project_settings\.config)", e["nome"]):
            e["dados"] += saida
        if e.get("texto") is not None:
            e["texto"] += saida
        c = e.get("camada")
        if c is not None and not c.pronta:
            c.alimentar(saida)
            if c.camadas is not None and self.camadas_impressao and c.camadas != self.camadas_impressao:
                e["camada"] = e["texto"] = None  # é outra placa do projeto: segue para a próxima
            elif c.pronta:
                self.camada1, self.placa, self.gcode = c, e["gcode"], e["texto"]

    def _fechar(self, e: dict, fim: int) -> None:
        if e["dados"]:
            self.pequenos[e["nome"]] = bytes(e["dados"])
        if self.gcode is not None and e.get("texto") is self.gcode:
            self.parado = True  # o G-code da placa acabou: o resto só interessa à leitura completa
        # pula o descritor (16 bytes; 24 em zip64) até o próximo cabeçalho
        for tam in (16, 24, 12, 20, 0):
            if self.buf[fim + tam:fim + tam + 2] == b"PK" or len(self.buf) < fim + tam + 2:
                self.pos = fim + tam
                break
        else:
            self.pos = fim + 16
        self.entrada = None

    def resultado_rapido(self, nome: str) -> dict | None:
        c = self.camada1
        if not c or not c.ordem:
            return None
        try:
            caixas = json.loads(self.pequenos.get(f"Metadata/plate_{self.placa}.json") or b"{}").get("bbox_objects") or []
        except ValueError:
            caixas = []
        try:
            proj = json.loads(self.pequenos.get("Metadata/project_settings.config") or b"{}")
        except ValueError:
            proj = {}
        centros = {pid: (sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts))
                   for pid, pts in c.pontos.items() if pts}
        casadas = _caixas_por_posicao(centros, caixas)
        pecas = []
        for pid in c.ordem:
            cx = casadas.get(pid)
            p = {"id": pid, "nome": _SEM_EXTENSAO.sub("", cx.get("name", "")) if cx else f"Peça {len(pecas) + 1}"}
            if cx:
                p["caixa"] = [round(float(v), 1) for v in cx["bbox"][:4]]
            pecas.append(p)
        return {"arquivo": nome, "placa": self.placa, "marcado": True, "mesa": _mesa(proj), "camadas": c.camadas,
                "pecas": pecas[:200], "vista": self.vista_parcial(), "vista_pendente": True}

    def vista_parcial(self) -> dict:
        """Prévia 3D das camadas que já chegaram (o G-code vem de baixo para cima)."""
        if not self.gcode:
            return {}
        try:
            return trajetos.extrair(bytes(self.gcode))
        except Exception:
            return {}


def _completo(conteudo: bytes, nome: str, camadas_impressao) -> dict:
    """Leitura completa (o arquivo inteiro já baixado): nomes oficiais e prévia 3D."""
    z = zipfile.ZipFile(io.BytesIO(conteudo))
    nomes = z.namelist()
    placas = sorted(int(m.group(1)) for n in nomes if (m := re.match(r"Metadata/plate_(\d+)\.gcode$", n)))
    if not placas:
        raise ValueError("o arquivo em impressão não tem G-code de placa")

    def camadas_de(n):
        with z.open(f"Metadata/plate_{n}.gcode") as g:
            m = re.search(rb"; total layer number: (\d+)", g.read(256 * 1024))
        return int(m.group(1)) if m else None

    placa = placas[0]  # arquivo exportado/enviado traz só a placa que está imprimindo
    if len(placas) > 1 and camadas_impressao:  # projeto com várias placas fatiadas: a do nº de camadas
        placa = next((n for n in placas if camadas_de(n) == camadas_impressao), placa)

    info = z.read("Metadata/slice_info.config").decode("utf-8", "replace") if "Metadata/slice_info.config" in nomes else ""
    bloco = next((b for b in re.findall(r"<plate>[\s\S]*?</plate>", info)
                  if re.search(rf'key="index" value="{placa}"', b)), info)
    marcado = re.search(r'key="label_object_enabled" value="true"', bloco) is not None
    pecas = [{"id": int(i), "nome": _SEM_EXTENSAO.sub("", n)}
             for i, n in re.findall(r'<object identify_id="(\d+)" name="([^"]*)"', bloco)]
    try:
        proj = json.loads(z.read("Metadata/project_settings.config"))
    except (KeyError, ValueError):
        proj = {}
    gcode = z.read(f"Metadata/plate_{placa}.gcode")
    m = re.search(rb"; total layer number: (\d+)", gcode[:256 * 1024])
    camadas = int(m.group(1)) if m else None
    try:
        vista = trajetos.extrair(gcode)
    except Exception as e:  # a prévia 3D é um extra: sem ela o mapa 2D continua
        print(f"prévia 3D: {e!r}")
        vista = {}

    # retângulos: onde cada peça está de verdade (centro das paredes no G-code) → plate_N.json
    try:
        caixas = json.loads(z.read(f"Metadata/plate_{placa}.json")).get("bbox_objects") or []
    except (KeyError, ValueError):
        caixas = []
    centros = {}
    for pid, linhas in (vista.get("pecas") or {}).items():
        xs = [v for _c, _z, xy in linhas for v in xy[0::2]]
        ys = [v for _c, _z, xy in linhas for v in xy[1::2]]
        if xs:
            centros[int(pid)] = (sum(xs) / len(xs) / 10, sum(ys) / len(ys) / 10)
    casadas = _caixas_por_posicao(centros, caixas)
    if not pecas:  # sem slice_info: as peças marcadas no G-code
        pecas = [{"id": pid, "nome": _SEM_EXTENSAO.sub("", casadas[pid].get("name", "")) if pid in casadas else f"Peça {i + 1}"}
                 for i, pid in enumerate(sorted(centros))]
    for p in pecas:
        if p["id"] in casadas:
            p["caixa"] = [round(float(v), 1) for v in casadas[p["id"]]["bbox"][:4]]
    return {"arquivo": nome, "placa": placa, "marcado": marcado, "mesa": _mesa(proj), "camadas": camadas,
            "pecas": pecas[:200], "vista": vista, "vista_pendente": False}


def _guardar(cache: Path, chave: str, dados: dict) -> None:
    try:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / f"{chave}.json").write_text(json.dumps(dados, separators=(",", ":")), encoding="utf-8")
        antigos = sorted(cache.glob("*.json"), key=lambda f: f.stat().st_mtime)[:-30]  # guarda os 30 mais recentes
        for f in antigos:
            f.unlink(missing_ok=True)
    except OSError:
        pass


def ler(ip: str, access_code: str, nome_arquivo: str, camadas_impressao: int | None = None,
        ao_parcial=None, cache: str | Path | None = None) -> dict:
    """Peças do arquivo em impressão. ao_parcial(dados) é chamado assim que a lista fica pronta
    (em segundos), sem a prévia 3D (vista_pendente). cache: pasta para guardar o resultado —
    o mesmo arquivo (nome, tamanho e data no cartão) não é baixado de novo."""
    nome = nome_arquivo.rsplit("/", 1)[-1]
    cache = Path(cache) if cache else None
    ftp = envio.conectar(ip, access_code)
    try:
        for caminho in (nome, f"cache/{nome}", f"/{nome}"):
            try:
                tamanho = ftp.size(caminho)
            except Exception:
                continue
            try:
                data = ftp.sendcmd(f"MDTM {caminho}")[4:].strip()
            except Exception:
                data = ""
            chave = hashlib.sha1(f"{ip}|{caminho}|{tamanho}|{data}|{camadas_impressao}".encode()).hexdigest()[:20]
            if cache and (cache / f"{chave}.json").exists():
                try:
                    dados = json.loads((cache / f"{chave}.json").read_text(encoding="utf-8"))
                    (cache / f"{chave}.json").touch()
                    return dados
                except (OSError, ValueError):
                    pass
            leitor = _LeitorEmAndamento(camadas_impressao)
            rapido = None
            ultimo = 0.0
            trava = threading.Lock()   # o 3D parcial é montado numa thread: não atrasa o download
            acabou = threading.Event()  # e nunca é entregue depois do resultado completo

            def entregar_parcial() -> None:
                if not trava.acquire(blocking=False):
                    return
                try:
                    if acabou.is_set():
                        return
                    parcial = {**rapido, "vista": leitor.vista_parcial(),
                               "recebido": round(100 * len(leitor.buf) / max(1, tamanho))}
                    if not acabou.is_set():
                        ao_parcial(parcial)
                except Exception as e:
                    print(f"pular peças (parcial): {e!r}")
                finally:
                    trava.release()

            def chegou(bloco: bytes) -> None:
                nonlocal rapido, ultimo
                leitor.alimentar(bloco)
                if not ao_parcial or leitor.camada1 is None or acabou.is_set():
                    return
                if rapido is None:
                    rapido = leitor.resultado_rapido(nome)
                    if rapido:
                        ultimo = time.time()
                        rapido["recebido"] = round(100 * len(leitor.buf) / max(1, tamanho))
                        try:
                            ao_parcial(rapido)
                        except Exception as e:
                            print(f"pular peças (lista rápida): {e!r}")
                elif time.time() - ultimo > 20 and not leitor.parado:  # o 3D vai crescendo
                    ultimo = time.time()
                    threading.Thread(target=entregar_parcial, daemon=True).start()

            inicio = time.time()
            try:
                ftp.retrbinary(f"RETR {caminho}", chegou, blocksize=64 * 1024)
            finally:
                acabou.set()
            with trava:  # espera um 3D parcial que esteja sendo entregue
                pass
            dados = _completo(bytes(leitor.buf), nome, camadas_impressao)
            dados["segundos"] = round(time.time() - inicio)
            if cache:
                _guardar(cache, chave, dados)
            return dados
        raise FileNotFoundError(f"não achei {nome} no cartão da impressora")
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()
