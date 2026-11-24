; Windows installer of Stock Terminal (Inno Setup 6). Built by .github/workflows/build-app.yml:
;     iscc /DAppVersion=1.0.0 packaging\installer.iss
; Packs dist\Stock Terminal\ (made by PyInstaller). Installs per user (no administrator needed), so "Update now"
; in the app can run a newer installer by itself. The user's data lives in %LOCALAPPDATA%\Stock Terminal\data and is
; never touched by installing, updating or uninstalling.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E3B1F0A-5C2D-4B8E-9A71-3F0D2C6B8E14}
AppName=Stock Terminal
AppVersion={#AppVersion}
AppPublisher=The Stock Terminal authors
DefaultDirName={localappdata}\Programs\Stock Terminal
DefaultGroupName=Stock Terminal
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Stock-Terminal-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\Stock Terminal.exe
LicenseFile=..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Check: not DesktopShortcutExists

[Code]
// a desktop shortcut called Stock Terminal already exists (for example of a code copy): do not offer to replace it
function DesktopShortcutExists(): Boolean;
begin
  Result := FileExists(ExpandConstant('{autodesktop}\Stock Terminal.lnk'));
end;

[Files]
Source: "..\dist\Stock Terminal\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; an update replaces the program files completely (old screen files would pile up otherwise)
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\Stock Terminal"; Filename: "{app}\Stock Terminal.exe"
Name: "{autodesktop}\Stock Terminal"; Filename: "{app}\Stock Terminal.exe"; Tasks: desktopicon

[Run]
; after a normal install: offer to open it; after an update from inside the app (silent): open it again
Filename: "{app}\Stock Terminal.exe"; Description: "Open Stock Terminal"; Flags: nowait postinstall skipifsilent
Filename: "{app}\Stock Terminal.exe"; Flags: nowait; Check: WizardSilent

[UninstallRun]
; remove the app's own scheduled tasks only (a code copy on the same PC has tasks with the same names that run python.exe)
Filename: "powershell.exe"; Parameters: "-NoProfile -NonInteractive -Command ""Get-ScheduledTask -TaskName 'Stock Terminal*' -ErrorAction SilentlyContinue | Where-Object {{ $_.Actions[0].Execute -like '*stockterminal-run*' }} | Unregister-ScheduledTask -Confirm:$false"""; Flags: runhidden; RunOnceId: "deltasks"
