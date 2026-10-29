# Assinatura pelo SignPath Foundation

Configurações para cadastrar no projeto `printbridge` do SignPath (Artifact configurations):

- `executaveis.xml` → slug **executaveis**: os dois `.exe`, num `.zip`, antes de montar o instalador
- `instalador.xml` → slug **instalador**: o instalador do Inno Setup

Signing policy usada pelos releases: **release-signing** (cada versão precisa de aprovação manual).

No GitHub (Settings → Secrets and variables → Actions):

- segredo `SIGNPATH_API_TOKEN`: token de um usuário com permissão de *submitter* no projeto
- variável `SIGNPATH_ORGANIZATION_ID`: o ID da organização no SignPath

Enquanto o segredo não existir, o workflow gera os arquivos sem assinatura, como antes.
