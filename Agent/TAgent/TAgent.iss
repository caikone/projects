; TAgent Inno Setup 6 安装包脚本
; 用法：用 Inno Setup Compiler 打开本文件，按 Ctrl+F9 编译

#define MyAppName "TAgent"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "TYY"
#define MyAppExeName "TAgent.exe"

[Setup]
; AppId 必须唯一，一次生成后不要改（改了会变成另一个软件）
AppId={{B8F3A2E1-7C4D-4E9A-9F2B-TAGENT20261027}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\TAgent
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=TAgent_Setup_v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
; ChineseSimplified.isl 未安装时用默认英文向导
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加图标:"; Flags: checkedonce

[Files]
; 把 PyInstaller 产出的整个 dist\TAgent 目录原样打进安装包
Source: "E:\projects\Agent\TAgent\dist\TAgent\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
; 开始菜单快捷方式 —— WorkingDir 必须设为 {app}，否则程序找不到 config.ini
Name: "{group}\TAgent"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\卸载 TAgent"; Filename: "{uninstallexe}"
; 桌面快捷方式
Name: "{commondesktop}\TAgent"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; 装完可选立即启动
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 TAgent(&R)"; Flags: nowait postinstall skipifsilent
