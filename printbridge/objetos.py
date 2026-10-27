"""Peças (objetos) da impressão atual, para o "Pular peças" do painel.

A impressora só informa o NOME do arquivo que está imprimindo e a lista de peças já puladas
(s_obj). As peças em si estão no arquivo, no cartão SD: baixamos uma cópia (FTPS, só
leitura) e lemos do .gcode.3mf:
- slice_info.config → cada peça com identify_id (o número que o comando skip_objects usa)
  e nome; e se o fatiamento marcou os objetos (label_object_enabled) — sem isso não dá
  para pular;
- plate_N.json → retângulo de cada peça na mesa (mm), para desenhar o mapa;
- project_settings.config → tamanho da mesa;
- o próprio G-code → contorno 3D de cada peça (trajetos.py), para a prévia 3D da mesa.
"""

import io
import json
import re
import zipfile

from . import envio
from . import trajetos


def _baixar(ip: str, access_code: str, nome: str) -> bytes:
    ftp = envio.conectar(ip, access_code)
    try:
        for caminho in (nome, f"cache/{nome}", f"/{nome}"):
            buf = io.BytesIO()
            try:
                ftp.retrbinary(f"RETR {caminho}", buf.write)
                return buf.getvalue()
            except Exception:
                continue
        raise FileNotFoundError(f"não achei {nome} no cartão da impressora")
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()


def _mesa(proj: dict) -> list[float]:
    """printable_area: ["0x0","256x0","256x256","0x256"] → [256, 256]"""
    try:
        pts = [tuple(float(v) for v in p.split("x")) for p in proj.get("printable_area") or []]
        return [max(x for x, _ in pts), max(y for _, y in pts)]
    except (ValueError, TypeError):
        return [256.0, 256.0]


def ler(ip: str, access_code: str, nome_arquivo: str) -> dict:
    nome = nome_arquivo.rsplit("/", 1)[-1]
    z = zipfile.ZipFile(io.BytesIO(_baixar(ip, access_code, nome)))
    nomes = z.namelist()
    placas = sorted(int(m.group(1)) for n in nomes if (m := re.match(r"Metadata/plate_(\d+)\.gcode$", n)))
    if not placas:
        raise ValueError("o arquivo em impressão não tem G-code de placa")
    placa = placas[0]  # arquivo exportado/enviado traz só a placa que está imprimindo

    info = z.read("Metadata/slice_info.config").decode("utf-8", "replace")
    bloco = next((b for b in re.findall(r"<plate>[\s\S]*?</plate>", info)
                  if re.search(rf'key="index" value="{placa}"', b)), info)
    marcado = re.search(r'key="label_object_enabled" value="true"', bloco) is not None
    pecas = [{"id": int(i), "nome": re.sub(r"\.(stl|3mf|obj|step)$", "", n, flags=re.I)}
             for i, n in re.findall(r'<object identify_id="(\d+)" name="([^"]*)"', bloco)]

    # retângulos: plate_N.json, casados pela ordem/nome (os ids desse arquivo são outros)
    try:
        caixas = json.loads(z.read(f"Metadata/plate_{placa}.json")).get("bbox_objects") or []
    except KeyError:
        caixas = []
    livres = list(caixas)
    for p in pecas:
        c = next((c for c in livres if re.sub(r"\.(stl|3mf|obj|step)$", "", c.get("name", ""), flags=re.I) == p["nome"]), None)
        if c is None and livres:
            c = livres[0]
        if c is not None:
            livres.remove(c)
            p["caixa"] = [round(float(v), 1) for v in c.get("bbox", [])[:4]]

    try:
        proj = json.loads(z.read("Metadata/project_settings.config"))
    except (KeyError, ValueError):
        proj = {}
    # nº de camadas do cabeçalho do G-code: confere que este é MESMO o arquivo em impressão
    # (projetos com várias placas geram arquivos de mesmo nome, e a impressora imprime da cópia
    # que guardou em /cache — o arquivo da raiz pode já ser de outra placa)
    gcode = z.read(f"Metadata/plate_{placa}.gcode")
    m = re.search(rb"; total layer number: (\d+)", gcode[:256 * 1024])
    camadas = int(m.group(1)) if m else None
    try:
        vista = trajetos.extrair(gcode)
    except Exception as e:  # a prévia 3D é um extra: sem ela o mapa 2D continua
        print(f"prévia 3D: {e!r}")
        vista = {}
    return {"arquivo": nome, "placa": placa, "marcado": marcado, "mesa": _mesa(proj), "camadas": camadas,
            "pecas": pecas[:200], "vista": vista}
