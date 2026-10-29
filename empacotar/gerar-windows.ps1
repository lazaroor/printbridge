# Gera os executáveis do PrintBridge para Windows (e o instalador, se o Inno Setup 6 estiver instalado).
#   PrintBridge.exe           o aplicativo: servidor + painel no navegador + ícone na bandeja
#   PrintBridge-servidor.exe  só o servidor, com console (mini PC, servidor; aceita --rede)
# Uso: powershell -File empacotar\gerar-windows.ps1     (resultado em dist\)
$ErrorActionPreference = "Continue"  # avisos do pip no stderr não são erro; conferimos $LASTEXITCODE
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
$versao = (Select-String -Path pyproject.toml -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value

py -m pip install -q --disable-pip-version-check paho-mqtt websocket-client pillow cryptography aiortc pystray pyinstaller
if ($LASTEXITCODE -ne 0) { throw "falha ao instalar as dependências" }

$comum = @("--noconfirm", "--clean", "--onefile", "--icon", "$raiz\empacotar\icone.ico",
           "--add-data", "$raiz\printbridge\web;printbridge\web", "--paths", $raiz, "--collect-submodules", "printbridge",
           "--distpath", "dist", "--workpath", "build", "--specpath", "build")
py -m PyInstaller @comum --windowed --name PrintBridge empacotar\entrada_app.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou (aplicativo)" }
py -m PyInstaller @comum --console --name PrintBridge-servidor empacotar\entrada_servidor.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou (servidor)" }

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
Get-ChildItem dist -File | Select-Object Name, @{n="MB"; e={[math]::Round($_.Length / 1MB, 1)}}
