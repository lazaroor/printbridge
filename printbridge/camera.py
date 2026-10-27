"""Câmera das impressoras: guarda sempre o último quadro JPEG.

- Bambu P1P / P1S / A1 / A1 mini: protocolo próprio em TLS na porta 6000 (JPEG puro).
- Bambu X1 / H2D e afins: RTSPS na porta 322, convertido em JPEG pelo ffmpeg.
  (Na impressora, ative "LAN Mode Liveview" para o RTSP funcionar.)
- Creality e Klipper: MJPEG por HTTP (printer.camera_url()).
- Creality novas (K1C 2025, K2, Hi, K1C/K1 Max 1.3.5.22+): WebRTC (webrtc_cam.py).
- Anycubic (Modo LAN): H.264 em HTTP-FLV, ligado por comando MQTT (anycubic.py).

A câmera só liga quando alguém está assistindo e desliga sozinha depois de IDLE_SECONDS.
"""

import shutil
import socket
import ssl
import struct
import subprocess
import threading
import time

IDLE_SECONDS = 30


class Camera:
    def __init__(self, printer):
        self.printer = printer
        self.frame: bytes | None = None
        self.frame_time = 0.0
        self.error = ""
        self._last_request = 0.0
        self._thread: threading.Thread | None = None
        self._cond = threading.Condition()
        self._tipo = None  # modo da câmera em uso (rtsp / http / webrtc / flv / jpeg)

    def touch(self) -> None:
        """Chamado a cada pedido de imagem; liga a câmera se estiver parada."""
        self._last_request = time.time()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def wait_frame(self, after: float, timeout: float = 10) -> tuple[bytes | None, float]:
        """Espera um quadro mais novo que `after`."""
        self.touch()
        with self._cond:
            self._cond.wait_for(lambda: self.frame_time > after, timeout=timeout)
            return self.frame, self.frame_time

    def _idle(self) -> bool:
        return time.time() - self._last_request > IDLE_SECONDS

    def _push(self, jpeg: bytes) -> None:
        with self._cond:
            self.frame = jpeg
            self.frame_time = time.time()
            self.error = ""
            self._cond.notify_all()
        if self._tipo and hasattr(self.printer, "camera_ok"):
            self.printer.camera_ok(self._tipo)  # Creality: este modo (MJPEG/WebRTC) funciona

    def _run(self) -> None:
        while not self._idle():
            tipo = self._tipo = self.printer.camera_type
            try:
                if tipo == "rtsp":
                    self._run_rtsp()
                elif tipo == "http":
                    self._run_http()
                elif tipo == "webrtc":
                    from . import webrtc_cam  # aiortc só carrega se houver câmera WebRTC
                    webrtc_cam.assistir(self.printer.camera_url(), self._push, self._idle)
                    if not self._idle():
                        raise RuntimeError("o vídeo WebRTC foi encerrado")
                elif tipo == "flv":  # Anycubic: a própria impressora liga, lê e converte o vídeo
                    self.printer.assistir_camera(self._push, self._idle)
                    if not self._idle():
                        raise RuntimeError("o vídeo da câmera foi encerrado")
                elif tipo == "nenhuma":
                    raise RuntimeError("esta impressora não informou câmera")
                else:
                    self._run_jpeg()
            except Exception as e:  # rede caiu, impressora desligada etc.
                self.error = str(e)
                print(f"[{self.printer.name}] câmera ({tipo}): {e}")
                if hasattr(self.printer, "camera_falhou"):
                    self.printer.camera_falhou(tipo)  # Creality: depois de 2 falhas tenta o outro modo
                time.sleep(3)

    # ---------- Creality / Klipper: MJPEG por HTTP (mjpg-streamer, crowsnest…) ----------

    def _run_http(self) -> None:
        url = self.printer.camera_url()
        if not url:
            raise RuntimeError("câmera não encontrada nesta impressora")
        import urllib.request
        with urllib.request.urlopen(url, timeout=10) as r:
            buf = b""
            while not self._idle():
                chunk = r.read1(65536)  # o que já chegou — read() esperaria juntar 64 KB
                if not chunk:
                    raise RuntimeError("a câmera encerrou o vídeo")
                buf += chunk
                # recorta JPEGs completos (FFD8 ... FFD9) do fluxo multipart; foto única também serve
                while True:
                    ini = buf.find(b"\xff\xd8")
                    fim = buf.find(b"\xff\xd9", ini + 2)
                    if ini < 0 or fim < 0:
                        break
                    self._push(buf[ini:fim + 2])
                    buf = buf[fim + 2:]
                if len(buf) > 8 << 20:  # nunca achou fim de quadro: não é MJPEG
                    raise RuntimeError("a câmera não mandou imagens no formato esperado")

    # ---------- P1 / A1 (porta 6000) ----------

    def _run_jpeg(self) -> None:
        p = self.printer
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        auth = struct.pack("<IIII", 0x40, 0x3000, 0, 0)
        auth += b"bblp".ljust(32, b"\0") + p.access_code.encode().ljust(32, b"\0")

        with socket.create_connection((p.ip, 6000), timeout=10) as raw:
            with ctx.wrap_socket(raw, server_hostname=p.ip) as s:
                s.sendall(auth)
                while not self._idle():
                    header = _recv_exact(s, 16)
                    size = struct.unpack("<I", header[:4])[0]
                    jpeg = _recv_exact(s, size)
                    if jpeg[:2] == b"\xff\xd8":
                        self._push(jpeg)

    # ---------- X1 / H2D (RTSP via ffmpeg) ----------

    def _run_rtsp(self) -> None:
        p = self.printer
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg não encontrado no PATH (necessário para câmera RTSP)")
        url = f"rtsps://bblp:{p.access_code}@{p.ip}:322/streaming/live/1"
        proc = subprocess.Popen(
            [ffmpeg, "-loglevel", "error", "-rtsp_transport", "tcp", "-i", url,
             "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "5", "-r", "5", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        buf = b""
        try:
            while not self._idle():
                chunk = proc.stdout.read(65536)
                if not chunk:
                    raise RuntimeError("stream RTSP encerrado (LAN Mode Liveview está ativo?)")
                buf += chunk
                # Recorta JPEGs completos (FFD8 ... FFD9) do fluxo.
                while True:
                    start = buf.find(b"\xff\xd8")
                    end = buf.find(b"\xff\xd9", start + 2)
                    if start < 0 or end < 0:
                        break
                    self._push(buf[start:end + 2])
                    buf = buf[end + 2:]
        finally:
            proc.kill()


def _recv_exact(s, n: int) -> bytes:
    data = bytearray()
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            raise ConnectionError("câmera fechou a conexão")
        data += chunk
    return bytes(data)
