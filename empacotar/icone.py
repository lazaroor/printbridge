"""Gera o ícone do PrintBridge (printbridge/web/icone.png e empacotar/icone.ico).
Uma ponte sobre um bico de impressora, desenhada com Pillow — sem arte de terceiros."""
from pathlib import Path
from PIL import Image, ImageDraw

RAIZ = Path(__file__).resolve().parent.parent
N = 512
img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((16, 16, N - 16, N - 16), radius=110, fill=(37, 99, 235))
branco = (255, 255, 255)
# ponte: arco + tabuleiro + pilares
d.arc((86, 150, N - 86, 470), start=180, end=360, fill=branco, width=34)
d.rectangle((70, 300, N - 70, 334), fill=branco)
for x in (150, 220, 292, 362):
    d.rectangle((x - 9, 200 if 180 < x < 330 else 240, x + 9, 300), fill=branco)
# bico (hotend) pingando filamento
d.polygon([(206, 362), (306, 362), (276, 410), (236, 410)], fill=branco)
d.ellipse((244, 420, 268, 444), fill=(255, 196, 61))
img.save(RAIZ / "printbridge" / "web" / "icone.png")
img.save(RAIZ / "empacotar" / "icone.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("ícone gerado")
