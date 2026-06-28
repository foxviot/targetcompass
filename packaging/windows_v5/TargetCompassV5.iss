; TargetCompass V5 formal Windows installer definition.
; Build with Inno Setup Compiler when available:
;   ISCC.exe TargetCompassV5.iss
;
; The PowerShell installer remains the canonical install action. This .iss wraps
; the payload into a standard setup wizard, Start Menu entries, desktop icon,
; and uninstall entry for professor/demo machines.

#define MyAppName "TargetCompass V5"
#define MyAppVersion "0.5.0-local"
#define MyAppPublisher "TargetCompass"
#define MyAppExeName "Launch-TargetCompassV5.ps1"

[Setup]
AppId={{9C4DA73E-1A86-4E68-A70E-2D678779B5E1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\TargetCompassV5
DefaultGroupName=TargetCompass V5
DisableProgramGroupPage=no
OutputDir=..\..\dist
OutputBaseFilename=TargetCompassV5_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\TargetCompassV5.ico

[Files]
Source: "Install-TargetCompassV5.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "Launch-TargetCompassV5.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "TargetCompassV5-Launcher.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "Stop-TargetCompassV5.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "Restart-TargetCompassV5.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "Repair-TargetCompassV5.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "Uninstall-TargetCompassV5.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "README_CN.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "payload\*"; DestDir: "{app}\payload"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "runtime_cache\*"; DestDir: "{app}\runtime_cache"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
Source: "wheelhouse\*"; DestDir: "{app}\wheelhouse"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
Name: "{group}\TargetCompass V5"; Filename: "{app}\TargetCompassV5-Launcher.cmd"; WorkingDir: "{app}"
Name: "{group}\Repair TargetCompass V5"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\Repair-TargetCompassV5.ps1"""; WorkingDir: "{app}"
Name: "{group}\Uninstall TargetCompass V5"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\Uninstall-TargetCompassV5.ps1"""; WorkingDir: "{app}"
Name: "{autodesktop}\TargetCompass V5"; Filename: "{app}\TargetCompassV5-Launcher.cmd"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: checkedonce

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\Install-TargetCompassV5.ps1"" -InstallDir ""{app}"" -SkipDemoInit -SkipDependencyInstall -SkipShortcutInstall -DependencyTimeoutSeconds 240 -DemoTimeoutSeconds 120"; WorkingDir: "{app}"; StatusMsg: "Installing TargetCompass V5 runtime and preparing demo project..."; Flags: runhidden waituntilterminated
Filename: "{app}\TargetCompassV5-Launcher.cmd"; WorkingDir: "{app}"; Description: "Launch TargetCompass V5"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\Uninstall-TargetCompassV5.ps1"" -InstallDir ""{app}"" -PreserveInstallerRoot"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "TargetCompassV5Cleanup"
