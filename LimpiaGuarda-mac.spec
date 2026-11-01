# Receta de PyInstaller para armar LimpiaGuarda.app en una Mac.
# Uso (en la Mac): python3 -m PyInstaller --noconfirm LimpiaGuarda-mac.spec
import re
from PyInstaller.utils.hooks import collect_all

VERSION = re.search(r'^VERSION = "(.+)"', open("limpia_guarda.py", encoding="utf-8").read(), re.M).group(1)

datas, binaries, hiddenimports = collect_all("tkinterdnd2")
datas += [("limpiateska.png", ".")]

a = Analysis(["limpia_guarda.py"], datas=datas, binaries=binaries, hiddenimports=hiddenimports)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LimpiaGuarda", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="LimpiaGuarda")
app = BUNDLE(
    coll,
    name="LimpiaGuarda.app",
    icon="limpiateska.icns",
    bundle_identifier="com.teskadev.limpiaguarda",
    info_plist={
        "CFBundleDisplayName": "Limpia&Guarda",
        "CFBundleShortVersionString": VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        # aparece cuando la app le pide al Finder vaciar la papelera
        "NSAppleEventsUsageDescription": "Limpia&Guarda usa el Finder para vaciar la papelera cuando se lo pedís.",
        "NSDesktopFolderUsageDescription": "Para analizar qué ocupa espacio en tu Escritorio.",
        "NSDocumentsFolderUsageDescription": "Para analizar qué ocupa espacio en Documentos.",
        "NSDownloadsFolderUsageDescription": "Para encontrar instaladores y comprimidos viejos en Descargas.",
        "NSRemovableVolumesUsageDescription": "Para guardar tus archivos en el disco externo.",
        "NSNetworkVolumesUsageDescription": "Para guardar tus archivos en un disco de red.",
    },
)
