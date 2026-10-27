"""Câmera WebRTC das Creality novas (K1C/K1 Max no firmware 1.3.5.22+, K1C 2025, K2, Hi).

Elas não têm mais o MJPEG da porta 8080: o vídeo sai por WebRTC, negociado num serviço da
própria impressora (porta 8000). Fazemos a mesma negociação que a página da impressora:
- criamos uma conexão só de RECEBER vídeo e juntamos os candidatos de rede (sem "trickle");
- POST http://<ip>:8000/call/webrtc_local, Content-Type text/plain, corpo =
  base64( JSON {"type":"offer","sdp": ...} );
- a resposta é base64 de JSON com "sdp" (ou de SDP puro) → é a "answer".
Os quadros H.264 recebidos viram JPEG (~1 por segundo) para o resto do app, igual às outras
câmeras. Protocolo seguido dos projetos k2dash e ha_creality_ws (modo webrtc_direct).

Usa aiortc + PyAV (decodificador embutido; não precisa de ffmpeg instalado).
NÃO TESTADO EM IMPRESSORA REAL — só contra um simulador do serviço de sinalização.
"""

import asyncio
import base64
import io
import json
import time
import urllib.request


def _codificar_oferta(sdp: str) -> bytes:
    return base64.b64encode(json.dumps({"type": "offer", "sdp": sdp}).encode())


def _decodificar_resposta(texto: str) -> str:
    texto = texto.strip()
    if texto.startswith("v=0"):
        return texto
    bruto = base64.b64decode(texto + "=" * (-len(texto) % 4)).decode("utf-8", "replace")
    try:
        return json.loads(bruto).get("sdp") or bruto
    except ValueError:
        return bruto


def _sinalizar(url: str, corpo: bytes, timeout: float = 15) -> str:
    origem = url.split("/call/")[0]
    req = urllib.request.Request(url, data=corpo, method="POST", headers={
        "Content-Type": "text/plain", "Accept": "*/*", "Origin": origem, "Referer": origem + "/"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        texto = r.read().decode("utf-8", "replace")
    if not texto or texto.strip() == "{}":
        raise RuntimeError("a impressora recusou a conexão de vídeo (outra tela já está assistindo?)")
    return texto


async def _sessao(url: str, ao_quadro, parar, intervalo: float) -> None:
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))  # mesma rede: não precisa de STUN
    chegou = asyncio.get_running_loop().create_future()

    @pc.on("track")
    def _faixa(track):
        if track.kind == "video" and not chegou.done():
            chegou.set_result(track)

    try:
        pc.addTransceiver("video", direction="recvonly")
        await pc.setLocalDescription(await pc.createOffer())  # aiortc já junta todos os candidatos aqui
        resposta = await asyncio.to_thread(_sinalizar, url, _codificar_oferta(pc.localDescription.sdp))
        await pc.setRemoteDescription(RTCSessionDescription(sdp=_decodificar_resposta(resposta), type="answer"))
        track = await asyncio.wait_for(chegou, 15)
        ultimo = 0.0
        while not parar():
            quadro = await asyncio.wait_for(track.recv(), 10)
            if time.time() - ultimo < intervalo:
                continue  # decodifica tudo (precisa), mas só converte ~1 por segundo
            ultimo = time.time()
            buf = io.BytesIO()
            quadro.to_image().save(buf, "JPEG", quality=75)
            ao_quadro(buf.getvalue())
    finally:
        await pc.close()


def assistir(url: str, ao_quadro, parar, intervalo: float = 0.8) -> None:
    """Bloqueia enquanto o vídeo chega; sai (com erro) se a conexão cair. Roda numa thread."""
    asyncio.run(_sessao(url, ao_quadro, parar, intervalo))
