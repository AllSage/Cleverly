#define MyAppName "Cleverly"
#ifndef MyAppVersion
#define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{7F97C744-7A57-4C63-9AC1-5E8DF7146128}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=AllSage
AppPublisherURL=https://github.com/AllSage/Cleverly
AppSupportURL=https://github.com/AllSage/Cleverly
AppUpdatesURL=https://github.com/AllSage/Cleverly
UninstallDisplayName={#MyAppName}
DefaultDirName={localappdata}\Programs\Cleverly
DefaultGroupName=Cleverly
DisableProgramGroupPage=yes
OutputBaseFilename=CleverlySetup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\Cleverly-App.cmd
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany=AllSage
VersionInfoProductName={#MyAppName}
VersionInfoDescription=Cleverly Offline AI Application
SetupLogging=yes

[Files]
Source: "..\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion; Excludes: ".git\*,.pytest_cache\*,__pycache__\*,venv\*,node_modules\*,data\*,logs\*,dist\*,*.pyc"

[Icons]
Name: "{autoprograms}\Cleverly"; Filename: "{app}\Cleverly-App.cmd"; WorkingDir: "{app}"
Name: "{autoprograms}\Cleverly Standalone"; Filename: "{app}\Cleverly-Standalone.cmd"; WorkingDir: "{app}"
Name: "{autodesktop}\Cleverly"; Filename: "{app}\Cleverly-App.cmd"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\Cleverly-App.cmd"; Description: "Launch Cleverly"; Flags: nowait postinstall skipifsilent
