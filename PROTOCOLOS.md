# Como cada marca conecta (referência para desenvolvedores)

Cada marca tem um adaptador em `printbridge/` com a mesma interface (`start/stop/summary/command`,
e `imprimir_arquivo` e `camera_url` quando a marca permite). Para incluir uma marca: escreva o
adaptador (veja `klipper.py` como exemplo curto), ligue em `frota.criar`/`frota.testar` e na busca
(`descoberta.py`), e acrescente a ficha em `marcas.py` com `"situacao": "pronto"`.

| Marca | Situação | Protocolo local | O que a pessoa informa | Busca na rede |
|---|---|---|---|---|
| Bambu Lab | pronto (testado) | MQTT TLS 8883 (`bblp` + access code), FTPS 990, câmera TLS 6000 / RTSPS 322 | IP, série, access code; LAN Only + Developer Mode | SSDP 2021 + varredura 8883 (série no certificado) |
| Creality (fábrica) | pronto (simulador) | WebSocket 9999 (`wsslicer`), upload HTTP 80, câmera MJPEG 8080 ou WebRTC 8000 | IP | varredura 9999 |
| Klipper / Moonraker | pronto (simulador) | HTTP 7125 | IP, porta, API key opcional (`trusted_clients`) | varredura 7125 |
| Flashforge AD5X / 5M | pronto (simulador) | HTTP 8898: `/detail`, `/control`, `/uploadGcode` (série + checkCode em todo pedido); câmera MJPEG 8080 | IP, série, código do modo “somente LAN” | varredura 8898 |
| Anycubic Kobra 3 / S1 / S1 Max / 4 / X (fábrica, Modo LAN) | pronto (simulador) — NÃO TESTADO EM IMPRESSORA REAL | HTTP 18910 `/info` → POST `/ctrl` assinado → AES → MQTT TLS 9883; envio HTTP 18910 `/gcode_upload`; câmera HTTP-FLV 18088 (detalhes na seção Anycubic abaixo) | IP | varredura 18910 (`/info` traz o modelo) |
| Anycubic com Rinkhals (Kobra 2 Pro/Plus/Max, ou alternativa) | pronto, pela ficha Klipper | igual ao Klipper | IP, porta | varredura 7125 |
| Elegoo Centauri Carbon | pronto (simulador) — NÃO TESTADO EM IMPRESSORA REAL | SDCP v3 (`elegoo.py`): WebSocket `ws://IP:3030/websocket`, JSON `{Id, Data:{Cmd, Data, RequestID, MainboardID, TimeStamp, From}, Topic:"sdcp/request/<MainboardID>"}`; respostas em `sdcp/response/…` (`Data.Data.Ack`), estado empurrado em `sdcp/status/…`, atributos em `sdcp/attributes/…`; `ping`→`pong`. Cmd 0 estado · 1 atributos · 128 imprimir `{Filename:"/local/<nome>", StartLayer:0}` · 129 pausar · 130 parar · 131 continuar · 386 vídeo `{Enable:1}`→`VideoUrl` · 403 controle (`TempTargetNozzle`, `TempTargetHotbed`, `PrintSpeedPct` 50–160, `LightStatus.SecondLight`). Estado FDM: `CurrentStatus` (lista, 1 = imprimindo) + `PrintInfo.Status` (tabela FDM: 5/6 pausa, 9 concluída, 13 imprimindo, 14 erro…), `Progress`, `CurrentTicks/TotalTicks` em segundos, `TempOfNozzle/TempOfHotbed/TempOfBox`. Câmera MJPEG `http://IP:3031/video` (depois do Cmd 386). Upload: POST `http://IP:3030/uploadFile/upload` em pedaços de 1 MB, multipart `S-File-MD5`, `Check`, `Offset`, `Uuid`, `TotalSize`, `File`; ok = `{"code":"000000"}`. Fontes: [SDCP V3.0.0 (cbd-tech)](https://github.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0) (envelope, tópicos, Cmd, upload; o documento é das resina), [elegoo-homeassistant](https://github.com/danielcherubini/elegoo-homeassistant) (campos FDM, Cmd 403, tabela de status FDM, porta 3031/video), [OrcaSlicer ElegooLink.cpp](https://github.com/SoftFever/OrcaSlicer/blob/main/src/slic3r/Utils/ElegooLink.cpp) (upload em pedaços + Cmd 128 com `/local/`). **Centauri Carbon 2: outro protocolo** — MQTT na porta 1883 com o broker na própria impressora (“MQTT invertido”), câmera MJPEG `:8080/?action=stream`, Cmd próprios (257/260/500–505 AMS); só documentado, não implementado | IP | UDP 3000 (`M99999`) + varredura 3030 |
| Prusa (PrusaLink) | pronto (simulador) | HTTP 80, API v1: `GET /api/v1/status`, `GET /api/v1/job` (204 sem trabalho), `PUT /api/v1/job/{id}/pause\|resume`, `DELETE /api/v1/job/{id}`, `PUT /api/v1/files/usb/<nome>` com `Print-After-Upload: ?1`, `GET /api/v1/info`, `GET /api/version`. Digest (usuário fixo `maker`, sem qop) ou `X-Api-Key` com a mesma senha. Sem endpoint de temperatura/velocidade/G-code; câmera só no Prusa Connect. Fontes: [openapi.yaml do Prusa-Link-Web](https://github.com/prusa3d/Prusa-Link-Web/blob/master/spec/openapi.yaml), firmware Buddy [`prusa_link_api_v1.cpp`](https://github.com/prusa3d/Prusa-Firmware-Buddy/blob/master/lib/WUI/link_content/prusa_link_api_v1.cpp), [`req_parser.cpp`](https://github.com/prusa3d/Prusa-Firmware-Buddy/blob/master/lib/WUI/nhttp/req_parser.cpp) e [`status_page.cpp`](https://github.com/prusa3d/Prusa-Firmware-Buddy/blob/master/lib/WUI/nhttp/status_page.cpp) (cabeçalho `WWW-Authenticate: Digest`/`ApiKey`) | IP, usuário (`maker`), senha | varredura 80 + `/api/version` respondendo 401 Digest/ApiKey (thread à parte) |
| Ultimaker S | em breve | API HTTP; autorização apertando “Permitir” na tela | IP | — |
| Duet / RRF | em breve | API do Duet Web Control | IP, senha opcional | — |
| OctoPrint (USB) | em breve | API HTTP com API key | IP, API key | — |

## Anycubic — Modo LAN de fábrica (`anycubic.py`)
NÃO TESTADO EM IMPRESSORA REAL: só contra um simulador escrito a partir do protocolo abaixo.
Nada vem do software da Anycubic: a assinatura usa o `token` que a própria impressora devolve no
`/info`. Nenhum código de terceiros foi copiado; o adaptador foi escrito a partir das descrições.

1. `GET http://IP:18910/info` → `{token, cn, modelId, modelName, ctrlType: "lan"|"cloud", ctrlInfoUrl}`.
   `ctrlType` = `cloud` → Modo LAN desligado. Sem `token`/`ctrlInfoUrl` (Kobra 2) → handshake antigo, não suportado.
2. `POST {ctrlInfoUrl}?ts=<ms>&nonce=<6 alfanum>&sign=<…>&did=<32 A-Z0-9>`, com
   `sign = md5hex(md5hex(token[:16]) + ts + nonce)`. Do `ctrlInfoUrl` usamos só porta/caminho; o IP é sempre o digitado.
3. Resposta `{code: 200, data: {token: <local>, info: <base64>}}`; `info` = AES-128-CBC, chave `token[16:32]`,
   IV = `<local>` (16 bytes), PKCS7 → `{broker: "mqtts://IP:9883", username, password, deviceId, …}`.
4. MQTT 3.1.1 com TLS na 9883 (certificado autoassinado, sem certificado de cliente), usuário/senha do passo 3.
   A senha muda quando a impressora reinicia: sem conexão por 45 s, o adaptador refaz o handshake.
- Assina `anycubic/anycubicCloud/v1/printer/public/{modelId}/{deviceId}/#` (respostas em `…/{tipo}/report`;
  `…/response` só confirma o `msgid`).
- Pedidos em `anycubic/anycubicCloud/v1/web/printer/{modelId}/{deviceId}/{tipo}`, corpo `{type, action, timestamp, msgid, data}`:
  `info|tempature|light` + `query` (sic, “tempature”); `multiColorBox` + `getInfo`; `print` + `pause|resume|stop` `{taskid}`;
  `print` + `update` `{taskid, settings: {target_nozzle_temp, target_hotbed_temp, print_speed_mode (1 silencioso, 2 padrão, 3 esportivo)}}`;
  `light` + `control` `{type: 2, status, brightness}`; `video` + `startCapture|stopCapture`.
- Estado: `info.data.state` (`free`/`busy`), `project.state` (`preheating → auto_leveling → … → printing → paused → stoped/finished`),
  `project.pause`, `remain_time` em minutos. O `info` só chega sozinho de tempos em tempos (pedimos a cada 10 s);
  `tempature` e `print` (com `progress`) chegam a cada mudança.
- Envio: `POST` multipart (`filename` + arquivo no campo `gcode`) em `info.urls.fileUploadurl`
  (`http://IP:18910/gcode_upload?s=<token>`), resposta `{code: 200, data: {gcode: <nome>}}`. Depois `print` + `start`
  **no tópico `…/slicer/printer/…`** com `{taskid: "-1", url: "", filename, md5, filetype: 1, project_type: 1, filesize,
  ams_settings, task_settings}`.
- Câmera: depois do `startCapture`, H.264 em HTTP-FLV em `info.urls.rtspUrl` (`http://IP:18088/flv`); Kobra 4 / X mandam
  um endereço com token na resposta `video/report`. Lido pelo PyAV (`camera.py`, tipo `flv`).
- Fontes (comunidade, sem SDK oficial):
  [anycubic_ha_local — PROTOCOL-VALIDATED.md](https://github.com/chrisfore/anycubic_ha_local/blob/main/research/PROTOCOL-VALIDATED.md)
  (validado numa S1 Max e por usuários em Kobra 3, 4 e X),
  [kobra-connect — docs/mqtt-commands.md](https://github.com/rvanderp3/kobra-connect/blob/main/docs/mqtt-commands.md),
  [kobra-lan-cli](https://github.com/eduardobittencourt/kobra-lan-cli) (envio + `print/start` validados numa Kobra X),
  [anycubic-orca-plugin](https://github.com/ianloic/anycubic-orca-plugin), [anycubic_kobrax](https://github.com/stribor/anycubic_kobrax).
- Incertezas: `stop` foi deduzido pela comunidade (não capturado); o `print/start` só foi validado numa Kobra X com um slot;
  sem ACE usamos `use_ams: false` (não confirmado); Kobra 3 sem câmera acessório só dá erro na câmera.
