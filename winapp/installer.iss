; SRT Generator — Windows installer (Inno Setup 6), DECISIONS.md V4.7.
; Built by make_release_windows.ps1:  ISCC /DVersion=4.7 /DStage=<staged app folder> /DOut=<dir> installer.iss
;
; Per-user, no admin: the app goes to %LOCALAPPDATA%\Programs\SRT Generator; everything it writes
; (python, venv, models, jobs, corrections, settings) to %LOCALAPPDATA%\SRT Generator, which updates
; never touch. WinSparkle runs this Setup.exe with /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- and
; asks the app to quit; [Run] reopens the app afterwards (in silent mode too: no skipifsilent).

#ifndef Version
  #error Pass /DVersion=<x.y>
#endif
#ifndef Stage
  #error Pass /DStage=<staged app folder>
#endif
#ifndef Out
  #define Out "."
#endif

[Setup]
AppId={{6C2B9E0A-5F4C-4E3B-9C2A-7D1E8B4A2F60}
AppName=SRT Generator
AppVersion={#Version}
AppVerName=SRT Generator {#Version}
AppPublisher=BrightLife Creations
AppPublisherURL=https://github.com/brightlifecreations-projects/song-srt-generator
AppSupportURL=https://github.com/brightlifecreations-projects/song-srt-generator/issues
AppUpdatesURL=https://github.com/brightlifecreations-projects/song-srt-generator/releases
VersionInfoVersion={#Version}
DefaultDirName={localappdata}\Programs\SRT Generator
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Out}
OutputBaseFilename=SRT-Generator-{#Version}-Setup
SetupIconFile={#Stage}\winapp\app.ico
UninstallDisplayIcon={app}\SRT Generator.exe
UninstallDisplayName=SRT Generator
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; Inno's RedirectionGuard mitigation is inherited by everything Setup starts when it isn't elevated,
; including the app reopened by [Run] after an install or an update, and it then can't follow the
; junction uv makes for the app's Python ("untrusted mount point", seen in testing). It guards
; elevated installs against privilege escalation; this one never elevates (per user, no admin).
RedirectionGuard=no
CloseApplications=no
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
; Files of the previous version that are no longer shipped (the folder is rebuilt from the stage).
Type: filesandordirs; Name: "{app}\winapp"
Type: filesandordirs; Name: "{app}\frontend"

[Icons]
Name: "{userprograms}\SRT Generator"; Filename: "{app}\SRT Generator.exe"; WorkingDir: "{app}"; AppUserModelID: "BrightLifeCreations.SRTGenerator"
Name: "{userdesktop}\SRT Generator"; Filename: "{app}\SRT Generator.exe"; WorkingDir: "{app}"; AppUserModelID: "BrightLifeCreations.SRTGenerator"; Tasks: desktopicon

[Run]
Filename: "{app}\SRT Generator.exe"; Description: "Open SRT Generator"; Flags: nowait postinstall

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
const
  AppMutex = 'SRTGeneratorApp';

function DataDir(): String;
begin
  Result := ExpandConstant('{localappdata}\SRT Generator');
end;

{ Stop the running app (the pid is in app.lock): used by the uninstaller, and by an update when the
  app didn't quit in time. }
procedure StopRunningApp();
var
  Lock: AnsiString;
  Pid: String;
  Code, I: Integer;
begin
  if not CheckForMutexes(AppMutex) then Exit;
  if LoadStringFromFile(DataDir() + '\app.lock', Lock) then
  begin
    Pid := Trim(String(Lock));
    if Pos(' ', Pid) > 0 then Pid := Copy(Pid, 1, Pos(' ', Pid) - 1);
    if Pid <> '' then
      Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /PID ' + Pid, '', SW_HIDE, ewWaitUntilTerminated, Code);
  end;
  for I := 1 to 50 do
  begin
    if not CheckForMutexes(AppMutex) then Exit;
    Sleep(100);
  end;
end;

function InitializeSetup(): Boolean;
var
  I: Integer;
begin
  Result := True;
  { An update: WinSparkle asked the app to quit just after starting this installer. Give it time. }
  for I := 1 to 100 do
  begin
    if not CheckForMutexes(AppMutex) then Exit;
    Sleep(100);
  end;
  if WizardSilent() then
    StopRunningApp()
  else if MsgBox('SRT Generator is open. Close it and continue?', mbConfirmation, MB_YESNO) = IDYES then
    StopRunningApp()
  else
    Result := False;
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  if CheckForMutexes(AppMutex) then
  begin
    if UninstallSilent() or
       (MsgBox('SRT Generator is open. Close it and uninstall?', mbConfirmation, MB_YESNO) = IDYES) then
      StopRunningApp()
    else
      Result := False;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    RegDeleteKeyIncludingSubkeys(HKCU, 'Software\BrightLife Creations\SRT Generator');
    RegDeleteKeyIfEmpty(HKCU, 'Software\BrightLife Creations');
    if DirExists(DataDir()) then
      { Default No: a silent uninstall keeps the data. }
      if SuppressibleMsgBox('Also delete your SRT Generator data?' + #13#10#13#10 +
                            DataDir() + #13#10#13#10 +
                            'It holds the AI models (about 3 GB), your jobs, corrections.md and settings. ' +
                            'Keep it if you might install SRT Generator again: nothing would need downloading.',
                            mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        DelTree(DataDir(), True, True, True);
  end;
end;
