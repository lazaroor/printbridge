"""Prévia 3D das peças a partir do G-code, para o "Pular peças".

O .gcode.3mf que vai para a impressora não leva as malhas (o Bambu Studio tira o modelo ao
exportar), mas o G-code tem cada peça marcada ("; start printing object, unique label id: N").
Pegamos só as paredes externas ("; FEATURE: Outer wall" / "Overhang wall") de cada peça,
camada por camada: empilhadas, desenham o contorno 3D real de cada uma — e como cada camada
leva o número, o site também mostra até onde já imprimiu.

Para caber numa resposta pequena: arcos (G2/G3) viram poucos pontos, cada linha é simplificada
(Ramer–Douglas–Peucker) e, se ainda passar do limite, usamos só uma camada a cada N.
Coordenadas em décimos de milímetro (inteiros), na mesa da impressora.
"""

import math
import re

PAREDES = ("Outer wall", "Overhang wall")
LIMITE_PONTOS = 60_000
TOLERANCIA = 0.25  # mm: quanto a linha simplificada pode se afastar da original
_NUM = re.compile(r"([XYZEIJ])(-?\d*\.?\d+)")


def _simplificar(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Ramer–Douglas–Peucker iterativo (a recursão estoura em contornos longos)."""
    if len(pts) < 3:
        return pts
    manter = [False] * len(pts)
    manter[0] = manter[-1] = True
    pilha = [(0, len(pts) - 1)]
    while pilha:
        a, b = pilha.pop()
        (ax, ay), (bx, by) = pts[a], pts[b]
        dx, dy = bx - ax, by - ay
        comp = math.hypot(dx, dy)
        pior, idx = 0.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            d = abs(dy * (px - ax) - dx * (py - ay)) / comp if comp > 1e-9 else math.hypot(px - ax, py - ay)
            if d > pior:
                pior, idx = d, i
        if pior > tol and idx > 0:
            manter[idx] = True
            pilha += [(a, idx), (idx, b)]
    return [p for p, m in zip(pts, manter) if m]


def _arco(x0, y0, x1, y1, i, j, horario: bool) -> list[tuple[float, float]]:
    cx, cy = x0 + i, y0 + j
    r = math.hypot(i, j)
    a0, a1 = math.atan2(y0 - cy, x0 - cx), math.atan2(y1 - cy, x1 - cx)
    if horario:
        while a1 >= a0:
            a1 -= 2 * math.pi
    else:
        while a1 <= a0:
            a1 += 2 * math.pi
    passos = max(2, min(24, int(abs(a1 - a0) * r / 0.8)))  # ~1 ponto a cada 0,8 mm de arco
    return [(cx + r * math.cos(a0 + (a1 - a0) * k / passos), cy + r * math.sin(a0 + (a1 - a0) * k / passos))
            for k in range(1, passos + 1)]


def extrair(gcode: bytes) -> dict:
    """{peça: [[camada, z, [x, y, x, y, ...]], ...]} — várias linhas por camada quando a parede é interrompida."""
    linhas_por_peca: dict[int, list] = {}
    x = y = z = 0.0
    relativo = False
    camada = 0
    peca = None
    parede = False
    atual: list[tuple[float, float]] | None = None  # linha que está sendo desenhada

    def fechar():
        nonlocal atual
        if atual and len(atual) > 1 and peca is not None:
            linhas_por_peca.setdefault(peca, []).append((camada, z, atual))
        atual = None

    for bruta in gcode.decode("utf-8", "replace").splitlines():
        if bruta.startswith(";"):
            if bruta.startswith("; CHANGE_LAYER"):
                fechar()
                camada += 1
            elif bruta.startswith("; FEATURE:"):
                fechar()
                parede = bruta[10:].strip() in PAREDES
            elif bruta.startswith("; start printing object, unique label id:"):
                fechar()
                peca = int(bruta.rsplit(":", 1)[1])
            elif bruta.startswith("; stop printing object"):
                fechar()
                peca = None
            continue
        cmd = bruta.split(";", 1)[0].strip()
        if not cmd:
            continue
        if cmd == "G90":
            relativo = False
            continue
        if cmd == "G91":
            relativo = True
            continue
        c = cmd[:3].rstrip()
        if c not in ("G0", "G1", "G2", "G3"):
            continue
        v = dict(_NUM.findall(cmd))
        nx, ny = x, y
        if "X" in v:
            nx = x + float(v["X"]) if relativo else float(v["X"])
        if "Y" in v:
            ny = y + float(v["Y"]) if relativo else float(v["Y"])
        if "Z" in v:
            z = z + float(v["Z"]) if relativo else float(v["Z"])
        extrusao = float(v.get("E", 0)) > 0 and ("X" in v or "Y" in v)
        if extrusao and parede and peca is not None and not relativo:
            if atual is None:
                atual = [(x, y)]
            if c in ("G2", "G3") and ("I" in v or "J" in v):
                atual += _arco(x, y, nx, ny, float(v.get("I", 0)), float(v.get("J", 0)), c == "G2")
            else:
                atual.append((nx, ny))
        elif "X" in v or "Y" in v:
            fechar()  # deslocamento sem extrusão: a parede foi interrompida
        x, y = nx, ny
    fechar()

    total = sum(len(p) for linhas in linhas_por_peca.values() for _c, _z, p in linhas)
    if not total:
        return {}
    simplificadas = {pid: [(cam, zz, _simplificar(p, TOLERANCIA)) for cam, zz, p in linhas]
                     for pid, linhas in linhas_por_peca.items()}
    pontos = sum(len(p) for linhas in simplificadas.values() for _c, _z, p in linhas)
    passo = max(1, math.ceil(pontos / LIMITE_PONTOS))
    ultima = max(cam for linhas in simplificadas.values() for cam, _z, _p in linhas)
    saida = {}
    for pid, linhas in simplificadas.items():
        # 1 camada a cada `passo`, sempre com a primeira e a última de cada peça
        cams = sorted({cam for cam, _z, _p in linhas})
        usar = {c for i, c in enumerate(cams) if i % passo == 0} | {cams[0], cams[-1]}
        saida[str(pid)] = [[cam, round(zz, 2), [round(v * 10) for xy in p for v in xy]]
                           for cam, zz, p in linhas if cam in usar]
    return {"camadas_total": ultima, "passo": passo, "pecas": saida}
