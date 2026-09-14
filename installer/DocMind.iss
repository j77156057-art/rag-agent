; Optional Inno Setup installer for the PyInstaller directory build.
#define AppName "DocMind"
#define AppVersion "0.1.0"
#define DistDir "..\dist\DocMind"

[Setup]
AppId={{8E3E8D6D-4D5E-4C85-9E9A-DOCMIND2026}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\DocMind
DefaultGroupName=DocMind
OutputDir=..\dist\installer
OutputBaseFilename=DocMind-Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=lowest
Uninstallable=yes

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autodesktop}\DocMind"; Filename: "{app}\DocMind.exe"
Name: "{group}\DocMind"; Filename: "{app}\DocMind.exe"

[Run]
Filename: "{app}\DocMind.exe"; Description: "启动 DocMind"; Flags: nowait postinstall skipifsilent
