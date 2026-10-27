"""Envio de arquivos para o cartão SD das Bambu pela rede (FTPS implícito, porta 990).

A Bambu exige duas coisas que o ftplib não faz sozinho:
- TLS desde o início da conexão (FTPS "implícito"), não o AUTH TLS do FTPS comum;
- a conexão de dados reaproveitando a sessão TLS da conexão de controle.
E o servidor dela não responde ao encerramento TLS do canal de dados, então não fazemos
`unwrap()` no fim do envio (senão o ftplib fica esperando para sempre).
"""

import ftplib
import ssl


def _contexto() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # certificado próprio da impressora
    return ctx


class _FTPSImplicito(ftplib.FTP_TLS):
    def __init__(self):
        super().__init__(context=_contexto(), timeout=30)
        self._sock = None

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, valor):
        if valor is not None and not isinstance(valor, ssl.SSLSocket):
            valor = self.context.wrap_socket(valor, server_hostname=self.host)
        self._sock = valor

    def ntransfercmd(self, cmd, rest=None):
        conn, tamanho = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, tamanho

    def storbinary(self, cmd, fp, blocksize=65536, callback=None, rest=None):
        self.voidcmd("TYPE I")
        with self.transfercmd(cmd, rest) as conn:
            while bloco := fp.read(blocksize):
                conn.sendall(bloco)
                if callback:
                    callback(bloco)
            # sem conn.unwrap(): a impressora não responde e o envio travaria aqui
        return self.voidresp()


def conectar(ip: str, access_code: str) -> _FTPSImplicito:
    ftp = _FTPSImplicito()
    ftp.connect(ip, 990)
    ftp.login("bblp", access_code)
    ftp.prot_p()
    return ftp


def enviar(ip: str, access_code: str, caminho_local: str, nome_remoto: str, progresso=None) -> None:
    """Grava o arquivo na raiz do cartão SD. `progresso(enviados, total)` a cada bloco."""
    import os
    total = os.path.getsize(caminho_local)
    enviados = 0

    def cb(bloco):
        nonlocal enviados
        enviados += len(bloco)
        if progresso:
            progresso(enviados, total)

    ftp = conectar(ip, access_code)
    try:
        with open(caminho_local, "rb") as f:
            ftp.storbinary(f"STOR {nome_remoto}", f, callback=cb)
    finally:
        try:
            ftp.quit()
        except Exception:
            ftp.close()
