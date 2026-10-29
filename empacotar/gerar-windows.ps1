# Gera os executáveis do PrintBridge para Windows (e o instalador, se o Inno Setup 6 estiver instalado).
#   PrintBridge.exe           o aplicativo: servidor + painel no navegador + ícone na bandeja
#   PrintBridge-servidor.exe  só o servidor, com console (mini PC, servidor; aceita --rede)
# Uso: powershell -File empacotar\gerar-windows.ps1     (resultado em dist\)
#   -Etapa exes      só os dois .exe        (no GitHub, eles são assinados entre uma etapa e outra)
#   -Etapa pacotes   só o .zip e o instalador, a partir dos .exe que já estão em dist\
param([ValidateSet("tudo", "exes", "pacotes")] [string] $Etapa = "tudo")
$ErrorActionPreference = "Continue"  # avisos do pip no stderr não são erro; conferimos $LASTEXITCODE
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
$versao = (Select-String -Path pyproject.toml -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value

if ($Etapa -ne "pacotes") {
  py -m pip install -q --disable-pip-version-check paho-mqtt websocket-client pillow cryptography aiortc pystray pyinstaller
  if ($LASTEXITCODE -ne 0) { throw "falha ao instalar as dependências" }

  # nome do produto e versão gravados no .exe (Propriedades → Detalhes); a assinatura pelo SignPath
  # confere que são "PrintBridge" e a versão do pyproject.toml
  New-Item -ItemType Directory -Force build | Out-Null
  $partes = ($versao.Split(".") + @("0", "0", "0"))[0..3] -join ", "
  foreach ($nome in "PrintBridge", "PrintBridge-servidor") {
    @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($partes), prodvers=($partes)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'PrintBridge'),
      StringStruct('FileDescription', '$(if ($nome -eq "PrintBridge") { "PrintBridge" } else { "PrintBridge (servidor)" })'),
      StringStruct('FileVersion', '$versao'),
      StringStruct('InternalName', '$nome'),
      StringStruct('OriginalFilename', '$nome.exe'),
      StringStruct('ProductName', 'PrintBridge'),
      StringStruct('ProductVersion', '$versao'),
      StringStruct('LegalCopyright', 'Apache-2.0 - github.com/lazaroor/printbridge')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Encoding utf8 "build\versao-$nome.txt"
  }

  $comum = @("--noconfirm", "--clean", "--onefile", "--icon", "$raiz\empacotar\icone.ico",
             "--add-data", "$raiz\printbridge\web;printbridge\web", "--paths", $raiz, "--collect-submodules", "printbridge",
             "--distpath", "dist", "--workpath", "build", "--specpath", "build")
  py -m PyInstaller @comum --windowed --name PrintBridge --version-file "$raiz\build\versao-PrintBridge.txt" empacotar\entrada_app.py
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou (aplicativo)" }
  py -m PyInstaller @comum --console --name PrintBridge-servidor --version-file "$raiz\build\versao-PrintBridge-servidor.txt" empacotar\entrada_servidor.py
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou (servidor)" }
}

if ($Etapa -ne "exes") {
  # versão portátil: os dois .exe + instruções, num .zip
  $zip = "dist\PrintBridge-$versao-windows-portatil.zip"
  Copy-Item empacotar\LEIA-ME.txt dist\LEIA-ME.txt -Force
  Compress-Archive -Force -Path dist\PrintBridge.exe, dist\PrintBridge-servidor.exe, dist\LEIA-ME.txt -DestinationPath $zip

  $iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
          Where-Object { Test-Path $_ } | Select-Object -First 1
  if ($iscc) {
    & $iscc "/DAppVersion=$versao" empacotar\instalador.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup falhou" }
  } else {
    Write-Host "Inno Setup 6 não encontrado: gerei só a versão portátil (.zip)."
  }
}
Get-ChildItem dist -File | Select-Object Name, @{n="MB"; e={[math]::Round($_.Length / 1MB, 1)}}
