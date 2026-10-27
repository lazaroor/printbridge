# PrintBridge

Controle local de impressoras 3D de várias marcas, numa interface só: estado, câmera,
pausar/continuar/parar, temperaturas, velocidade, luz e envio de arquivos para imprimir.
Tudo pela rede local, direto com a impressora: sem conta na nuvem do fabricante e sem trocar
o firmware.

Dá para usar de três jeitos:

1. **Aplicativo web pronto**: `python -m printbridge` abre um painel no navegador, com um
   assistente que mostra o passo a passo de cada marca.
2. **Embutido no seu sistema ou site**: um iframe ou uma linha de JavaScript mostram uma
   impressora (ou todas) dentro de outra página.
3. **API REST e biblioteca Python**: para quem quer montar a própria interface.

## Impressoras compatíveis

| Marca | Modelos | O que a pessoa informa | Testado em impressora real |
|---|---|---|---|
| Bambu Lab | A1, A1 mini, P1P/P1S/P2S, X1C/X1E, H2D/H2S | IP, número de série, access code (LAN Only + Developer Mode) | ✅ A1, A1 mini |
| Creality (firmware de fábrica) | K1, K1C, K1 Max, K1 SE, Ender-3 V3 / KE / Plus, K2, Hi | só o IP | ⚠️ só em simulador |
| Klipper / Moonraker | QIDI, Sovol, Elegoo Neptune 4, Voron, Creality com root, Sonic Pad, Kobra com Rinkhals | IP (+ API key, se ligada) | ⚠️ só em simulador |
| Flashforge | AD5X, Adventurer 5M / 5M Pro | IP, número de série, código do modo “somente LAN” | ⚠️ só em simulador |
| Anycubic (Modo LAN de fábrica) | Kobra 3 / 3 Max, S1 / S1 Max, Kobra 4, Kobra X | só o IP | ⚠️ só em simulador |
| Elegoo | Centauri Carbon | só o IP | ⚠️ só em simulador |
| Prusa (PrusaLink) | MK4, MK4S, MK3.9, MINI, XL, Core One | IP, usuário (`maker`) e senha | ⚠️ só em simulador |

“Só em simulador” quer dizer que o adaptador foi escrito a partir da documentação pública do
protocolo e de pesquisas da comunidade, e testado contra um simulador desse protocolo — **ainda
não numa impressora de verdade**. Se você tem uma, testes, relatos e correções são muito bem-vindos.

O que cada marca permite (câmera, luz, temperaturas, velocidade, envio de arquivo) está na
ficha dela, em `printbridge/marcas.py` (`recursos`). Como cada protocolo funciona, com as
fontes: [PROTOCOLOS.md](PROTOCOLOS.md).

## Instalar e abrir

```bash
pip install -e ".[tudo]"
python -m printbridge
```

O painel abre em `http://localhost:8750`. Clique em **Adicionar impressora**, escolha a marca
e siga o passo a passo: a busca na rede encontra a impressora e o PrintBridge testa a conexão
antes de salvar.

- `python -m printbridge --rede` aceita também acessos de outros aparelhos da rede (tablet,
  outro computador), sempre com o token. Serve para deixar rodando num Raspberry Pi ou num
  mini PC da oficina.
- A configuração fica em `~/.printbridge/config.json` (ou na pasta de `--pasta` ou da variável
  `PRINTBRIDGE_DIR`). Ela guarda os access codes e as senhas: proteja essa pasta.
- Sem os extras (`pip install -e .`) tudo funciona, menos o Modo LAN da Anycubic
  (`cryptography`) e as câmeras WebRTC/H.264 (`aiortc`).

## Embutir em outro sistema ou site

1. No painel, abra **Configurações** e:
   - copie o **token**;
   - em **Sites que podem embutir**, coloque o endereço do seu sistema (ex.:
     `https://meusistema.com.br`).
2. Na página do seu sistema:

```html
<div id="impressora"></div>
<script src="http://IP-DO-PRINTBRIDGE:8750/printbridge.js"></script>
<script>
  PrintBridge.embutir("#impressora", {
    servidor: "http://IP-DO-PRINTBRIDGE:8750",
    token: "SEU_TOKEN",
    impressora: "ID_DA_IMPRESSORA", // sem isto, mostra todas
    controles: true,                // false = só olhar
    tema: "claro",                  // ou "escuro"
  });
</script>
```

O iframe ajusta a altura sozinho. Sem JavaScript, um iframe simples também serve:
`<iframe src="http://IP:8750/embed?impressora=ID&token=SEU_TOKEN" style="width:100%;height:520px;border:0"></iframe>`.
O botão **Embutir** de cada impressora, no painel, gera esse código já com o ID.

Para usar a API direto do navegador:

```js
const pb = PrintBridge.cliente("http://IP:8750", "SEU_TOKEN");
const lista = await pb.impressoras();
await pb.comando(lista[0].id, "pause");
const parar = pb.ouvir((lista) => console.log(lista)); // atualizações a cada 2 s
document.querySelector("img").src = pb.camera(lista[0].id); // vídeo da câmera
```

### Segurança

- No próprio computador, o painel funciona sem token. Qualquer outro acesso (outro aparelho,
  outro site, outro sistema) precisa dele: cabeçalho `Authorization: Bearer TOKEN` ou
  `?token=` no endereço, onde não dá para mandar cabeçalho (iframe, `<img>`, EventSource).
- Só os sites da lista conseguem pôr o PrintBridge num iframe (`frame-ancestors`) e ler a API
  pelo navegador (CORS).
- Pedidos que mudam algo exigem o token ou o cabeçalho `X-PrintBridge: 1`: um site qualquer
  aberto no navegador não consegue mandar comandos para as suas impressoras.
- Nenhuma resposta traz access codes, senhas ou API keys.
- Trocar o token em Configurações desliga na hora tudo que usava o antigo.

## API REST

Todas as respostas são JSON. De fora do computador, mande o token.

| Método | Endereço | O que faz |
|---|---|---|
| GET | `/api/impressoras` | estado de todas (mesmo formato para todas as marcas) |
| GET | `/api/impressoras/ID` | estado de uma |
| GET | `/api/eventos` | Server-Sent Events: a lista a cada 2 s |
| GET | `/api/impressoras/ID/camera.jpg` | uma foto da câmera |
| GET | `/api/impressoras/ID/camera.mjpeg` | vídeo MJPEG (serve direto num `<img>`) |
| POST | `/api/impressoras/ID/comando` | `{"acao": "pause"}`; ações: `pause`, `resume`, `stop`, `set_nozzle`, `set_bed`, `set_speed` (1–4), `light_on`, `light_off` (com `"valor"` quando precisa) |
| POST | `/api/impressoras/ID/imprimir?nome=peca.gcode` | corpo = o arquivo; envia e começa a imprimir (Bambu Lab: `.gcode.3mf`, `&slot=` 0–3 do AMS ou 254 da bobina externa) |
| POST | `/api/impressoras` | `{"marca": "creality", "campos": {"ip": "…", "name": "…"}}`: testa e adiciona |
| POST | `/api/testar` | mesmo corpo: só testa |
| POST | `/api/descobrir` | `{"marca": "bambu"}` (opcional): procura na rede |
| POST | `/api/impressoras/ID/nome` | `{"nome": "…"}` |
| DELETE | `/api/impressoras/ID` | remove |
| GET | `/api/marcas` | a ficha de cada marca: modelos, passo a passo, campos (para montar o seu assistente) |

```bash
curl -H "Authorization: Bearer SEU_TOKEN" http://IP:8750/api/impressoras
curl -X POST -H "Authorization: Bearer SEU_TOKEN" -H "Content-Type: application/json" \
     -d '{"acao":"pause"}' http://IP:8750/api/impressoras/ID/comando
```

## Biblioteca Python

```python
import printbridge

for a in printbridge.procurar(6):          # impressoras na rede local
    print(a["marca"], a["ip"], a["name"])

p = printbridge.criar({"id": "k1", "marca": "creality", "ip": "192.168.0.40"})
p.start()
print(p.summary())                          # o mesmo formato para todas as marcas
p.command("pause")
```

`printbridge.Gerenciador()` guarda as impressoras em disco e as mantém conectadas (é o que o
painel usa). `printbridge.ficha("anycubic")` devolve o que pedir à pessoa e o passo a passo na
tela da impressora. Veja também [examples/monitor.py](examples/monitor.py).

## Contribuir

Para acrescentar uma marca: escreva o adaptador com a mesma interface dos outros
(`klipper.py` é o exemplo mais curto), ligue em `frota.py` e na busca (`descoberta.py`) e
acrescente a ficha em `marcas.py`. Os detalhes estão em [PROTOCOLOS.md](PROTOCOLOS.md).

## Licença

Apache-2.0 — veja [LICENSE](LICENSE) e [NOTICE](NOTICE). Os nomes das marcas de impressoras
pertencem aos seus donos; o PrintBridge é independente e não tem ligação com nenhum fabricante.
