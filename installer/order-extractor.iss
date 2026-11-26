#ifdef LocalPreview
  #define ProductId "OrderExtractorPreview"
  #define ProductName "Order Extractor Preview"
#else
  #define ProductId "OrderExtractor"
  #define ProductName "Order Extractor"
#endif

[Setup]
AppId={#ProductId}
AppName={#ProductName}
AppVersion={#ReleaseVersion}
DefaultDirName={localappdata}\Programs\{#ProductName}
DefaultGroupName={#ProductName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=OrderExtractor-{#ReleaseVersion}-x64
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\OrderExtractorLauncher.exe
CloseApplications=yes

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"

[Files]
Source: "..\dist\OrderExtractorLauncher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\OrderExtractor\*"; DestDir: "{app}\versions\{#ReleaseVersion}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#ProductName}"; Filename: "{app}\OrderExtractorLauncher.exe"

[Run]
Filename: "{app}\OrderExtractorLauncher.exe"; Description: "Avvia {#ProductName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\versions"
Type: filesandordirs; Name: "{app}\pending-content"
Type: files; Name: "{app}\current.json"
Type: files; Name: "{app}\pending.json"
Type: files; Name: "{app}\requested-update.json"
Type: files; Name: "{app}\last-update.json"
Type: files; Name: "{app}\launch.lock"
Type: files; Name: "{app}\payload.lock"
Type: files; Name: "{app}\update.lock"
[Setup]
