; Inno Setup 6 Installer Script for VOX (VoxLocal)
; Compiles dist\VoxLocal\* into a single downloadable Windows installer (VoxLocal-Setup.exe)

#define MyAppName "VOX Voice Assistant"
#define MyAppShortName "VOX"
#ifndef MyAppVersion
  #define MyAppVersion "2.3.0"
#endif
#define MyAppPublisher "VOX Local AI"
#define MyAppURL "https://vox-voice-assistant.vercel.app/"
#define MyAppExeName "VoxLocal.exe"

[Setup]
AppId={{8D6E7B9C-4B28-4C31-98E2-2F19D8B6E4A1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\VOX
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\release_artifacts
OutputBaseFilename=VoxLocal-Setup
SetupIconFile=..\assets\favicon.ico
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
AppMutex=Local\VoxLocal_AI_Voice_Assistant_Mutex_v1

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "Launch VOX automatically when Windows starts"; GroupDescription: "Startup:"

[Files]
Source: "..\dist\VoxLocal\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "VoxLocalVoiceAssistant"; ValueData: """{app}\{#MyAppExeName}"" --startup --minimized"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
