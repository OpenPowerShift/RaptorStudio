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
ArchitecturesAllowed=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\raptor-studio.exe
ChangesAssociations=yes

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

[Registry]
; File types: a ProgID per format, then the extension pointing at it. The
; OpenWithProgids entries put RAPTOR Studio under "Open with" for both
; extensions. .rbd is only made the default when nothing owns it yet, so a
; machine with Raptor 7 keeps its association and Raptor 7 stays available.
Root: HKA; Subkey: "Software\Classes\RaptorStudio.rbds"; ValueType: string; ValueData: "RAPTOR Studio model"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\RaptorStudio.rbds\DefaultIcon"; ValueType: string; ValueData: "{app}\raptor-studio.exe,0"
Root: HKA; Subkey: "Software\Classes\RaptorStudio.rbds\shell\open\command"; ValueType: string; ValueData: """{app}\raptor-studio.exe"" ""%1"""
Root: HKA; Subkey: "Software\Classes\RaptorStudio.rbd"; ValueType: string; ValueData: "Raptor 7 model"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\RaptorStudio.rbd\DefaultIcon"; ValueType: string; ValueData: "{app}\raptor-studio.exe,0"
Root: HKA; Subkey: "Software\Classes\RaptorStudio.rbd\shell\open\command"; ValueType: string; ValueData: """{app}\raptor-studio.exe"" ""%1"""
Root: HKA; Subkey: "Software\Classes\.rbds"; ValueType: string; ValueData: "RaptorStudio.rbds"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.rbds\OpenWithProgids"; ValueType: string; ValueName: "RaptorStudio.rbds"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.rbd\OpenWithProgids"; ValueType: string; ValueName: "RaptorStudio.rbd"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.rbd"; ValueType: string; ValueData: "RaptorStudio.rbd"; Flags: createvalueifdoesntexist uninsdeletevalue

[Run]
Filename: "{app}\raptor-studio.exe"; Description: "Launch Raptor Studio"; \
    Flags: nowait postinstall skipifsilent
