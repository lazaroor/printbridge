; Instalador do PrintBridge para Windows (Inno Setup 6). Gerado por empacotar\gerar-windows.ps1.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "PrintBridge"
#define AppExe "PrintBridge.exe"

[Setup]
AppId={{6B0F6C52-9E7A-4F4B-9B0E-5A1C2D7E8F31}
AppName={#AppName}
AppVersion={#AppVersion}
; nome do produto e versão gravados no instalador (a assinatura pelo SignPath confere)
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Instalador do PrintBridge
VersionInfoCopyright=Apache-2.0 - github.com/lazaroor/printbridge
AppPublisher=PrintBridge
AppPublisherURL=https://github.com/lazaroor/printbridge
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=PrintBridge-{#AppVersion}-windows-instalador
SetupIconFile=icone.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
LicenseFile=..\LICENSE

[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "inicializacao"; Description: "Abrir o PrintBridge quando o Windows iniciar (fica no ícone ao lado do relógio)"
Name: "atalho"; Description: "Criar atalho na área de trabalho"

[Files]
Source: "..\dist\PrintBridge.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\PrintBridge-servidor.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "LEIA-ME.txt"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\PrintBridge"; Filename: "{app}\{#AppExe}"
Name: "{group}\PrintBridge (servidor, rede local)"; Filename: "{app}\PrintBridge-servidor.exe"; Parameters: "--rede"
Name: "{group}\Desinstalar o PrintBridge"; Filename: "{uninstallexe}"
Name: "{autodesktop}\PrintBridge"; Filename: "{app}\{#AppExe}"; Tasks: atalho
Name: "{userstartup}\PrintBridge"; Filename: "{app}\{#AppExe}"; Parameters: "--minimizado"; Tasks: inicializacao

[Run]
Filename: "{app}\{#AppExe}"; Description: "Abrir o PrintBridge agora"; Flags: nowait postinstall skipifsilent
