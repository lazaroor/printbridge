"""Catálogo de marcas do PrintBridge — a "ficha" de cada uma.

O assistente "Adicionar impressora" (painel.py) se monta a partir daqui: modelos, passo a
passo na impressora, campos que a pessoa preenche, se a busca na rede encontra, recursos.
Para uma marca nova: escreva o adaptador (como creality.py) e acrescente a ficha abaixo com
"situacao": "pronto". Fichas "em_breve" já aparecem no assistente explicando o que vão pedir.

Cada campo: id (chave na config da impressora), rotulo, tipo (texto | senha | numero),
obrigatorio, dica, padrao, tamanho (exato, opcional).
"""

IP = {"id": "ip", "rotulo": "IP da impressora", "tipo": "texto", "obrigatorio": True,
      "dica": "Aparece nas configurações de rede da impressora (ex.: 192.168.0.23)."}
NOME = {"id": "name", "rotulo": "Nome para reconhecer", "tipo": "texto", "obrigatorio": False,
        "dica": "Ex.: “A1 da bancada”. Se ficar vazio, usamos o da impressora."}

MARCAS = [
    {
        "id": "bambu", "nome": "Bambu Lab", "situacao": "pronto", "adaptador": "bambu",
        "conexao": "Rede local (MQTT + cartão SD pela rede)",
        "modelos": ["A1", "A1MINI", "P1P", "P1S", "P2S", "X1C", "X1E", "H2D", "H2S"],
        "descobre": True,
        "passos": [
            "Na tela da impressora, abra Configurações → Rede (WLAN).",
            "Ligue o LAN Only Mode.",
            "Ligue o Developer Mode (fica dentro do LAN Only Mode). Sem ele a impressora só deixa ver, não deixa pausar nem parar.",
            "Anote o Access Code (8 caracteres) que aparece nessa tela.",
            "X1 / H2: ligue também o LAN Mode Liveview para a câmera.",
            "Deixe um cartão SD na impressora (usado no Imprimir em lote e no Pular peças).",
        ],
        "campos": [IP,
                   {"id": "serial", "rotulo": "Número de série", "tipo": "texto", "obrigatorio": True,
                    "dica": "Vem sozinho quando você escolhe a impressora encontrada na rede. Na tela: Configurações → Dispositivo."},
                   {"id": "access_code", "rotulo": "Access Code", "tipo": "senha", "obrigatorio": True, "tamanho": 8,
                    "dica": "Tela da impressora → Configurações → Rede. Muda se você desligar e ligar o LAN Only Mode."},
                   NOME],
        "recursos": ["camera", "luz", "temperaturas", "velocidade", "pular", "lote"],
        "aviso": "Com o Developer Mode, o Bambu Handy para de funcionar fora de casa. O Bambu Studio continua imprimindo pela rede.",
    },
    {
        "id": "creality", "nome": "Creality", "situacao": "pronto", "adaptador": "creality",
        "conexao": "Rede local (a mesma do Creality Print) — sem root",
        "modelos": ["K1", "K1C", "K1MAX", "K1SE", "ENDER3V3", "ENDER3V3KE", "ENDER3V3PLUS", "K2PLUS", "K2PRO", "HI"],
        "descobre": True,
        "passos": [
            "Na tela da impressora, abra Configurações → Rede e conecte ao Wi-Fi da loja (o mesmo deste computador).",
            "Anote o IP que aparece nessa tela, caso a busca não encontre.",
            "Não precisa de root, de conta na Creality Cloud nem de senha.",
        ],
        "campos": [IP, NOME],
        "recursos": ["camera", "luz", "temperaturas", "velocidade", "lote"],
        "aviso": "Imprimir em lote: fatie no Creality Print/OrcaSlicer para o modelo e envie o .gcode. Reserve o IP no roteador — se ele mudar, adicione de novo.",
    },
    {
        "id": "klipper", "nome": "Klipper / Moonraker", "situacao": "pronto", "adaptador": "klipper",
        "conexao": "API do Moonraker (a do Mainsail/Fluidd)",
        "modelos": ["KLIPPER", "QIDI", "SOVOL", "NEPTUNE4", "VORON", "K1", "K1C", "K1MAX", "KOBRA3", "KOBRA2PRO", "KOBRAS1", "SONICPAD"],
        "descobre": True,
        "passos": [
            "Serve para impressoras com Klipper: QIDI (X-Plus 3, X-Max 3, Q1, Plus 4), Sovol (SV07, SV08), Elegoo Neptune 4, Voron, Creality com root, Sonic Pad e Anycubic Kobra com Rinkhals.",
            "Anycubic com Rinkhals (firmware da comunidade, alternativa ao Modo LAN; obrigatório nas Kobra 2 Pro/Plus/Max): o Moonraker fica na porta 7125. O Rinkhals não é oficial — considere a garantia.",
            "Confira se o Moonraker responde: no navegador, abra http://IP-DA-IMPRESSORA:7125/printer/info — deve aparecer \"state\": \"ready\".",
            "No moonraker.conf, em [authorization], deixe a rede da loja em trusted_clients (ex.: 192.168.0.0/24) — ou use uma API key.",
            "A webcam configurada no Mainsail/Fluidd aparece sozinha no painel.",
        ],
        "campos": [IP,
                   {"id": "porta", "rotulo": "Porta do Moonraker", "tipo": "numero", "obrigatorio": True, "padrao": "7125",
                    "dica": "Quase sempre 7125."},
                   {"id": "api_key", "rotulo": "API key", "tipo": "senha", "obrigatorio": False,
                    "dica": "Só se você ligou autenticação no Moonraker."},
                   NOME],
        "recursos": ["camera", "temperaturas", "velocidade", "lote"],
        "aviso": "Imprimir em lote: envie o .gcode fatiado para a impressora — ele já começa a imprimir.",
    },
    {
        "id": "anycubic", "nome": "Anycubic", "situacao": "pronto", "adaptador": "anycubic",
        "conexao": "Modo LAN da própria impressora (firmware de fábrica) — só o IP, sem root",
        "modelos": ["KOBRA3", "KOBRA3MAX", "KOBRAS1", "KOBRAS1MAX", "KOBRA4", "KOBRAX"],
        "descobre": True,
        "passos": [
            "Na tela da impressora: Configurações → Rede e conecte ao Wi-Fi da loja (o mesmo deste computador).",
            "Ainda em Configurações → Rede, ligue o Modo LAN (LAN Mode). Se aparecer um aviso sobre a nuvem, confirme.",
            "Anote o IP que aparece nessa tela, caso a busca não encontre.",
            "Não precisa de conta Anycubic, de senha nem de instalar firmware.",
            "Kobra 2 Pro / Plus / Max: o Modo LAN delas é diferente e ainda não é compatível — use o Rinkhals e escolha Klipper / Moonraker.",
            "Kobra, Kobra 2 comum, Neo e Go não têm rede: ainda não compatíveis.",
        ],
        "campos": [IP, NOME],
        "recursos": ["camera", "luz", "temperaturas", "velocidade", "lote"],
        "aviso": "Com o Modo LAN ligado, o app Anycubic na nuvem para de ver a impressora (o Anycubic Slicer Next continua imprimindo pela rede). Imprimir em lote: fatie no Anycubic Slicer Next/OrcaSlicer e envie o .gcode; sai com o slot carregado do ACE (ou o filamento do bico). Luz só na S1 / S1 Max. Reserve o IP no roteador — se ele mudar, adicione de novo.",
    },
    # ---- ainda não implementadas: aparecem no assistente com o que vão precisar ----
    {
        "id": "elegoo", "nome": "Elegoo", "situacao": "pronto", "adaptador": "elegoo",
        "conexao": "Centauri Carbon: rede local (SDCP, a mesma do OrcaSlicer/ElegooSlicer) — sem senha",
        "modelos": ["CENTAURICARBON"], "descobre": True,
        "passos": [
            "Na tela da Centauri Carbon: engrenagem → Rede → conecte ao Wi-Fi da loja (o mesmo deste computador).",
            "Anote o IP que aparece nessa tela, caso a busca não encontre.",
            "Não precisa de senha, de código nem de conta na Elegoo.",
            "Neptune 4 / 4 Pro / 4 Plus / 4 Max: volte e escolha Klipper / Moonraker.",
            "Centauri Carbon 2: ainda não compatível (usa outro protocolo).",
        ],
        "campos": [IP, NOME],
        "recursos": ["camera", "luz", "temperaturas", "velocidade", "lote"],
        "aviso": "Imprimir em lote: fatie no ElegooSlicer/OrcaSlicer para a Centauri Carbon e envie o .gcode (vai para a memória da impressora). A câmera aceita poucas pessoas assistindo ao mesmo tempo. Reserve o IP no roteador — se ele mudar, adicione de novo.",
    },
    {
        "id": "flashforge", "nome": "Flashforge", "situacao": "pronto", "adaptador": "flashforge",
        "conexao": "Modo “somente LAN” da própria impressora (API da porta 8898)",
        "modelos": ["AD5X", "AD5M", "AD5MPRO"], "descobre": True,
        "passos": [
            "Na tela da impressora: engrenagem → Wi-Fi → conecte ao Wi-Fi da loja (o mesmo deste computador).",
            "Ainda nas configurações de rede, ligue o modo “somente LAN” (LAN only).",
            "Anote o código de acesso que aparece nessa tela.",
            "Anote o número de série: engrenagem → Sobre (começa com SN…).",
        ],
        "campos": [IP,
                   {"id": "serial", "rotulo": "Número de série", "tipo": "texto", "obrigatorio": True,
                    "dica": "Tela da impressora → engrenagem → Sobre."},
                   {"id": "access_code", "rotulo": "Código de acesso", "tipo": "senha", "obrigatorio": True,
                    "dica": "Aparece ao ligar o modo “somente LAN”."},
                   NOME],
        "recursos": ["camera", "luz", "velocidade", "lote"],
        "aviso": "Com o “somente LAN”, o app Flashforge na nuvem para de ver a impressora. Imprimir em lote: fatie no Orca-Flashforge e envie o .gcode (ainda sem escolher o slot do IFS: sai com o filamento carregado). Temperatura ainda não muda pelo painel.",
    },
    {
        "id": "prusa", "nome": "Prusa", "situacao": "pronto", "adaptador": "prusa",
        "conexao": "PrusaLink da própria impressora (firmware 4.7 ou mais novo)",
        "modelos": ["MK4", "MK4S", "MK39", "MINI", "XL", "COREONE"], "descobre": True,
        "passos": [
            "Serve para MK4, MK4S, MK3.9, MINI/MINI+, XL e Core One com firmware 4.7 ou mais novo (atualize pelo pendrive se for mais antigo). MK3/MK3S+ não tem PrusaLink na própria placa: use OctoPrint.",
            "Na tela da impressora: Configurações → Rede → conecte ao Wi-Fi da loja (ou cabo de rede), o mesmo deste computador.",
            "Ainda em Configurações → Rede → PrusaLink: ligue o PrusaLink e anote o usuário (maker) e a senha que aparecem ali.",
            "Anote o IP que aparece em Configurações → Rede, caso a busca não encontre.",
            "Deixe um pendrive na impressora: o Imprimir em lote grava o arquivo nele.",
        ],
        "campos": [IP,
                   {"id": "usuario", "rotulo": "Usuário do PrusaLink", "tipo": "texto", "obrigatorio": True, "padrao": "maker",
                    "dica": "Quase sempre “maker”."},
                   {"id": "senha", "rotulo": "Senha do PrusaLink", "tipo": "senha", "obrigatorio": True,
                    "dica": "Tela da impressora → Configurações → Rede → PrusaLink."},
                   NOME],
        "recursos": ["lote"],
        "aviso": "Imprimir em lote: fatie no PrusaSlicer para o modelo e envie o .gcode ou .bgcode — ele já começa a imprimir. Câmera, temperatura e velocidade ainda não mudam pelo painel (a API local do PrusaLink não tem esse caminho; a câmera da Prusa só fala com o Prusa Connect). O Prusa Connect continua funcionando junto.",
    },
    {
        "id": "ultimaker", "nome": "Ultimaker", "situacao": "em_breve",
        "conexao": "API da impressora — autorização na tela",
        "modelos": ["S3", "S5", "S7"], "descobre": False,
        "passos": ["Impressora na rede da loja.", "Ao adicionar, aperte “Permitir” quando a impressora pedir na tela."],
        "campos": [IP, NOME], "recursos": [],
    },
    {
        "id": "duet", "nome": "Duet / RepRapFirmware", "situacao": "em_breve",
        "conexao": "API do Duet Web Control",
        "modelos": ["DUET3", "DUET2"], "descobre": False,
        "passos": ["Duet na rede da loja.", "Se você definiu senha no Duet Web Control, tenha ela em mãos."],
        "campos": [IP, {"id": "senha", "rotulo": "Senha (se tiver)", "tipo": "senha", "obrigatorio": False, "dica": ""}, NOME],
        "recursos": [],
    },
    {
        "id": "octoprint", "nome": "OctoPrint (impressora USB)", "situacao": "em_breve",
        "conexao": "API do OctoPrint num Raspberry Pi ligado à impressora pelo USB",
        "modelos": ["OCTOPRINT"], "descobre": False,
        "passos": ["Serve para impressoras sem rede (Ender-3 V2/S1, Prusa MK3, Kobra antigas…) ligadas a um Raspberry Pi com OctoPrint.",
                   "No OctoPrint: Configurações → API → copie a API key (ou crie uma em Application Keys)."],
        "campos": [IP, {"id": "api_key", "rotulo": "API key do OctoPrint", "tipo": "senha", "obrigatorio": True, "dica": ""}, NOME],
        "recursos": [],
    },
]

POR_ID = {m["id"]: m for m in MARCAS}


def publico() -> list[dict]:
    """O catálogo como o painel mostra (tudo aqui é público: não tem segredo nenhum)."""
    return MARCAS
