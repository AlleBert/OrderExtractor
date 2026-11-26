[Setup]
AppId=OrderExtractor
AppName=Order Extractor
AppVersion={#ReleaseVersion}
DefaultDirName={localappdata}\Programs\Order Extractor
DefaultGroupName=Order Extractor
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
Name: "{autoprograms}\Order Extractor"; Filename: "{app}\OrderExtractorLauncher.exe"

[Run]
Filename: "{app}\OrderExtractorLauncher.exe"; Description: "Avvia Order Extractor"; Flags: nowait postinstall skipifsilent

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
