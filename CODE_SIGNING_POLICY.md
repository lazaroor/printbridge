# Code signing policy (política de assinatura de código)

Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by [SignPath Foundation](https://signpath.org).

Os executáveis do PrintBridge para Windows (`PrintBridge.exe`, `PrintBridge-servidor.exe` e o
instalador) são gerados automaticamente pelo GitHub Actions a partir do código deste repositório
(`.github/workflows/release.yml`), a cada versão publicada em
[Releases](https://github.com/lazaroor/printbridge/releases). Só o que é gerado desse jeito é
assinado: nada é assinado a partir de um computador pessoal.

## Equipe

| Papel | Quem |
|---|---|
| Autores (podem alterar o código sem revisão) | [lazaroor](https://github.com/lazaroor) |
| Revisores (revisam contribuições de fora, como pull requests) | [lazaroor](https://github.com/lazaroor) |
| Aprovadores (aprovam cada pedido de assinatura) | [lazaroor](https://github.com/lazaroor) |

Todos os membros usam autenticação em dois fatores no GitHub e no SignPath. Cada versão só é
assinada depois da aprovação manual de um aprovador.

## Privacidade

O PrintBridge não envia nenhuma informação para outros sistemas em rede, a não ser quando o
usuário pede. Ele conversa só com as impressoras 3D que o próprio usuário adiciona, na rede local, e
com os sistemas que o usuário autorizar a usar a API ou embutir o painel (com o token e a lista de
sites liberados em Configurações). Não há telemetria, conta nem servidor na nuvem.

## Instalar e desinstalar

O instalador cria atalhos e, só se o usuário marcar a opção, abre o PrintBridge junto com o
Windows. Ele se desinstala pelo menu Iniciar ("Desinstalar o PrintBridge") ou em
Configurações → Aplicativos. A versão portátil não instala nada: basta apagar a pasta. A
configuração fica em `%USERPROFILE%\.printbridge`.
