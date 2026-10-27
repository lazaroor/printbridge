"""Envio de arquivo por HTTP multipart (Creality e Moonraker), em pedaços, com progresso —
sem carregar o G-code inteiro na memória e sem dependências externas."""

import http.client
import os
import secrets
import urllib.parse
from urllib.parse import quote  # noqa: F401  (usado por quem importa este módulo)


def post_multipart(url: str, campo: str, caminho: str, nome: str, extras: dict | None = None,
                   cabecalhos: dict | None = None, progresso=None, timeout: float = 300) -> str:
    u = urllib.parse.urlsplit(url)
    limite = "----printbridge" + secrets.token_hex(8)
    partes_extras = b"".join(
        f'--{limite}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, v in (extras or {}).items())
    cabeca = partes_extras + (
        f'--{limite}\r\nContent-Disposition: form-data; name="{campo}"; filename="{nome}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n").encode()
    rabo = f"\r\n--{limite}--\r\n".encode()
    total = os.path.getsize(caminho)

    con = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
    try:
        con.putrequest("POST", u.path + (f"?{u.query}" if u.query else ""))
        con.putheader("Content-Type", f"multipart/form-data; boundary={limite}")
        con.putheader("Content-Length", str(len(cabeca) + total + len(rabo)))
        for k, v in (cabecalhos or {}).items():
            con.putheader(k, v)
        con.endheaders()
        con.send(cabeca)
        enviados = 0
        with open(caminho, "rb") as f:
            while bloco := f.read(1 << 16):
                con.send(bloco)
                enviados += len(bloco)
                if progresso:
                    progresso(enviados, total)
        con.send(rabo)
        r = con.getresponse()
        corpo = r.read().decode("utf-8", "replace")
        if r.status >= 300:
            raise RuntimeError(f"a impressora respondeu {r.status} ao receber o arquivo: {corpo[:200]}")
        return corpo
    finally:
        con.close()
