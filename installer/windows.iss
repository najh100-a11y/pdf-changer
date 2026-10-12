; Inno Setup installer script (compiled by ISCC in GitHub Actions)
; Usage: ISCC /DMyAppVersion=1.0.0 installer\windows.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{6B2E9C41-7D3A-4F58-9E1B-2C8A5F0D7E93}
AppName=PDF2MD
AppVersion={#MyAppVersion}
AppPublisher=PDF2MD
DefaultDirName={autopf}\PDF2MD
DefaultGroupName=PDF2MD
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=PDF2MD-Setup-{#MyAppVersion}-windows
Compression=lzma2/fast
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
UninstallDisplayIcon={app}\PDF2MD.exe

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\PDF2MD\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PDF2MD"; Filename: "{app}\PDF2MD.exe"
Name: "{autodesktop}\PDF2MD"; Filename: "{app}\PDF2MD.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PDF2MD.exe"; Description: "{cm:LaunchProgram,PDF2MD}"; Flags: nowait postinstall skipifsilent
