; Inno Setup script for the Mezaniya Windows installer.
;
; build_installer.py runs PyInstaller first (dist\Mezaniya) and then compiles this file with
; the version from app\version.py:
;   ISCC /DAppVersion=0.1.0 /DAppVersionFull=0.1.0-beta installer.iss
; Output: Output\Mezaniya-Setup-v<AppVersionFull>.exe
;
; The program goes to Program Files; the user's data (database, mezaniya.env, backups, logs)
; lives in %LOCALAPPDATA%\Mezaniya and is only removed on uninstall if the user says so.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppVersionFull
  #define AppVersionFull AppVersion
#endif
#define AppName "Mezaniya"
#define AppExe "Mezaniya.exe"
#define ServerExe "MezaniyaServer.exe"
#define AppURL "https://github.com/Abdelrahman-Elsayed-2001/Mezaniya"

[Setup]
; never change AppId: upgrades and the uninstaller find the installed copy by it
AppId={{B48D777A-DFD5-44A5-9E44-08062A528EF1}
AppName={#AppName}
AppVersion={#AppVersionFull}
AppVerName={#AppName} {#AppVersionFull}
VersionInfoVersion={#AppVersion}
AppPublisher=Abdelrahman Elsayed
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
; the data folder is per user (LOCALAPPDATA) on purpose, even though setup runs as admin
UsedUserAreasWarning=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=LICENSE
SetupIconFile=packaging\mezaniya.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=Output
OutputBaseFilename=Mezaniya-Setup-v{#AppVersionFull}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=auto

[Languages]
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
arabic.DataTitle=بيانات الميزانية والإعدادات
arabic.DataText=هل تريد الاحتفاظ ببيانات الميزانية والإعدادات (قاعدة البيانات والنسخ الاحتياطية والسجلات)؟%n%nالمجلد: %1%n%nاحتفظ بها إذا كنت ستعيد تثبيت ميزانية لاحقًا؛ ستجدها كما هي.
arabic.KeepData=الاحتفاظ بالبيانات (Keep Data)
arabic.DeleteAll=حذف كل شيء (Delete Everything)
arabic.DeleteSure=سيُحذف المجلد التالي بكل ما فيه، ولا يمكن التراجع عن ذلك:%n%n%1%n%nهل أنت متأكد؟
english.DataTitle=Budget data and settings
english.DataText=Do you want to keep your budget data and settings (database, backups and logs)?%n%nFolder: %1%n%nKeep them if you may reinstall Mezaniya later; everything will be there as you left it.
english.KeepData=Keep Data
english.DeleteAll=Delete Everything
english.DeleteSure=This folder and everything in it will be deleted. This cannot be undone:%n%n%1%n%nAre you sure?

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; drop the previous version's runtime so no stale modules linger after an upgrade
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\Mezaniya\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
var
  DeleteUserData: Boolean;

function UserDataDir(): String;
begin
  Result := ExpandConstant('{localappdata}\{#AppName}');
end;

// The dashboard and the server lock files in the program folder: close them first.
procedure StopMezaniya();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#ServerExe} /IM {#AppExe}', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopMezaniya();
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    StopMezaniya();
    DeleteUserData := False;
    // a silent uninstall (e.g. from an upgrade script) always keeps the data
    if DirExists(UserDataDir()) and not UninstallSilent() then
      if TaskDialogMsgBox(CustomMessage('DataTitle'),
                          FmtMessage(CustomMessage('DataText'), [UserDataDir()]),
                          mbConfirmation, MB_YESNO, [CustomMessage('KeepData'), CustomMessage('DeleteAll')],
                          0) = IDNO then
        DeleteUserData := MsgBox(FmtMessage(CustomMessage('DeleteSure'), [UserDataDir()]),
                                 mbCriticalError, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  end
  else if (CurUninstallStep = usPostUninstall) and DeleteUserData then
    DelTree(UserDataDir(), True, True, True);
end;
