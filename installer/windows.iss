; Inno Setup script for RAPTOR Studio (Windows x64).
; Built in CI:  iscc /DAppVersion=x.y.z installer\windows.iss
; Requires the PyInstaller onedir output at dist\raptor-studio\.

#ifndef AppVersion
#define AppVersion "0.0.0"
#endif

[Setup]
AppId={{E59A4B09-4490-4658-9B61-A4673DD35B45}
AppName=Raptor Studio
AppVersion={#AppVersion}
AppPublisher=OpenPowerShift
; Shown on the installer's licence page; {app}\_internal holds NOTICE.md,
; THIRD_PARTY_NOTICES.md and the third-party licences/.
LicenseFile=..\LICENSE
DefaultDirName={autopf}\Raptor Studio
DefaultGroupName=Raptor Studio
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=raptor-studio-{#AppVersion}-windows-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
Architectures=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\raptor-studio.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Additional icons:"

[Files]
Source: "..\dist\raptor-studio\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Raptor Studio"; Filename: "{app}\raptor-studio.exe"
Name: "{autodesktop}\Raptor Studio"; Filename: "{app}\raptor-studio.exe"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\raptor-studio.exe"; Description: "Launch Raptor Studio"; \
    Flags: nowait postinstall skipifsilent
