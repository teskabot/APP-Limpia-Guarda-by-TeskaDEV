; Instalador de Windows (Inno Setup). Lo compila GitHub Actions:
;   iscc /DMyVersion=0.4.1 instalador-windows.iss
; Espera la app ya armada en dist\LimpiaGuarda\ (PyInstaller en modo carpeta).

#ifndef MyVersion
  #define MyVersion "0.0"
#endif

[Setup]
AppId={{6E0B7C1A-4B8E-4C55-9C1F-2A7F3D9E5B21}
AppName=Limpia&Guarda
AppVersion={#MyVersion}
AppPublisher=Teskadev
AppPublisherURL=https://github.com/teskabot/APP-Limpia-Guarda-by-TeskaDEV
; se instala para el usuario actual: no pide permisos de administrador
PrivilegesRequired=lowest
DefaultDirName={autopf}\LimpiaGuarda
DefaultGroupName=Limpia&Guarda
DisableProgramGroupPage=yes
SetupIconFile=limpiateska.ico
UninstallDisplayIcon={app}\LimpiaGuarda.exe
UninstallDisplayName=Limpia&Guarda
OutputDir=out
OutputBaseFilename=LimpiaGuarda-Windows-Instalador
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el Escritorio"

[Files]
Source: "dist\LimpiaGuarda\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Limpia&Guarda"; Filename: "{app}\LimpiaGuarda.exe"
Name: "{autodesktop}\Limpia&Guarda"; Filename: "{app}\LimpiaGuarda.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\LimpiaGuarda.exe"; Description: "Abrir Limpia&Guarda"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; al desinstalar se borran también las tareas programadas de la pestaña AUTO
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""\LimpiaGuarda\limpieza"" /F"; Flags: runhidden; RunOnceId: "QuitarLimpieza"
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""\LimpiaGuarda\sesiones"" /F"; Flags: runhidden; RunOnceId: "QuitarSesiones"
