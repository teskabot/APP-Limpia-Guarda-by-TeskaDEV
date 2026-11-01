# ▓▒░ Limpia&Guarda v0.4.1 · by Teskadev

**Analizá** qué te come el disco → **limpiá** lo que sobra → **guardá en el HDD** externo lo que no usás (verificado con SHA-256).
Sin fe, con hashes.

## Arrancar (Windows)

1. Instalá Python desde python.org. Tildá **"Add Python to PATH"**; tkinter ya viene incluido.
2. Opcional, para arrastrar y soltar carpetas: `pip install tkinterdnd2`
3. Doble clic en **`Abrir LimpiaGuarda.bat`**.

¿Lo querés como .exe? Doble clic en **`Crear EXE.bat`** y queda en `dist\LimpiaGuarda.exe`.

## Instaladores (Windows y Mac)

GitHub Actions (`.github/workflows/build.yml`) arma los instaladores en cada push a `main`. Quedan en la pestaña **Actions** → la última corrida → **Artifacts**:
- `LimpiaGuarda-Windows-Instalador.exe`: instalador de Windows (con [instalador-windows.iss](instalador-windows.iss), Inno Setup). Instala para tu usuario sin pedir administrador, crea los accesos directos y se desinstala desde Configuración → Aplicaciones.
- `LimpiaGuarda-Mac-AppleSilicon.dmg` y `LimpiaGuarda-Mac-Intel.dmg`.

Para publicar una versión: cambiá `VERSION` en `limpia_guarda.py`, hacé commit y subí un tag con ese número (`git tag v0.5` y `git push origin v0.5`). Se crea un **Release** con los tres instaladores y las instrucciones de qué bajar.

Links fijos a la última versión, para compartir:
- https://github.com/teskabot/APP-Limpia-Guarda-by-TeskaDEV/releases/latest/download/LimpiaGuarda-Windows-Instalador.exe
- https://github.com/teskabot/APP-Limpia-Guarda-by-TeskaDEV/releases/latest/download/LimpiaGuarda-Mac-AppleSilicon.dmg
- https://github.com/teskabot/APP-Limpia-Guarda-by-TeskaDEV/releases/latest/download/LimpiaGuarda-Mac-Intel.dmg

## Mac

Para instalarla:
1. Abrí el `.dmg` y arrastrá **LimpiaGuarda** a **Aplicaciones**. Tiene que estar ahí para que AUTO funcione.
2. La primera vez macOS la bloquea porque no está firmada por Apple. Abrila, cerrá el aviso y andá a **Ajustes del Sistema → Privacidad y seguridad → "Abrir igualmente"**.
3. Recomendado: en **Privacidad y seguridad → Acceso total al disco**, agregá LimpiaGuarda. Sin eso, macOS pregunta carpeta por carpeta y no deja ver el tamaño de la papelera.

Diferencias con Windows:
- **AUTO** usa launchd (`~/Library/LaunchAgents/com.teskadev.limpiaguarda.*.plist`). Si la Mac estaba dormida a esa hora, corre al despertarse. Si estaba apagada, no.
- **Papelera**: se vacía a través del Finder. La primera vez macOS pregunta si LimpiaGuarda puede controlar el Finder.
- **Temporales**: la carpeta temporal de tu usuario (`$TMPDIR`). **Caches**: pip, npm, Yarn, Homebrew, VS Code y Cursor en `~/Library`.
- **Nunca toca** `~/Library`, `/System`, `/Applications`, ni lo de adentro de "paquetes" como la biblioteca de Fotos o de Música (se cuentan, pero no se recomiendan). Los proyectos `.logicx`, `.band` y `.fcpbundle` se tratan como proyectos de DAW/video.
- Los archivos de iCloud/OneDrive que están solo en la nube no se cuentan ni se tocan, y GUARDAR avisa si algo está dentro de iCloud Drive.

También se puede correr desde una Mac con Python: `python3 limpia_guarda.py` (con Homebrew hace falta `brew install python-tk`).

## INICIO

Todo lo importante está en una sola pantalla:

1. Elegí la **CARPETA** (por defecto tu usuario; también podés elegir un disco entero) y dale a **▶ ANALIZAR**. Mira todo **sin tocar nada**.
2. Arriba aparecen tres números: **para limpiar**, **para guardar en el HDD** y **libres** en el disco. La barra muestra el disco: en verde lo que se liberaría limpiando, en celeste lo que se liberaría guardando en el HDD.
3. Abajo hay una **tarjeta por categoría**. Un clic la prende `[■]` o la apaga `[ ]`, y los números y botones se actualizan al toque. Tu elección se recuerda.
   - 🟢 **SEGURO** (prendidas por defecto): papelera, temporales con más de 1 día, caches de npm/pip/Python/Next/Vite/VS Code/Cursor, `node_modules`/`.venv` de proyectos quietos hace +30 días (solo si tienen `package.json`/`requirements`, así se reinstalan con un comando), instaladores e ISOs viejos en Descargas.
   - 🟡 **REVISAR** (apagadas por defecto): ZIP/RAR viejos en Descargas y duplicados exactos (mismo SHA-256).
   - 🔵 **→ HDD** (prendidas por defecto): proyectos de DAW/video sin tocar hace +6 meses (**como carpeta entera**, para no romper los samples) y WAV/video/RAW de 20 MB o más sin tocar hace +6 meses.
   - **ver ›** te lleva al detalle de esa categoría.
4. **✖ LIMPIAR X GB** borra lo de las tarjetas prendidas, con una sola confirmación que lista todo y avisa de cualquier riesgo.
5. **→ GUARDAR X GB EN EL HDD** mueve al HDD lo de las tarjetas prendidas. Si todavía no elegiste el disco, te lo pide. Las caches usadas en los últimos 2 días nunca se marcan, porque pueden estar abiertas.

Al terminar ves "✔ liberaste X" sobre la barra, y arriba a la derecha queda el contador de **liberado en total**.

### Duplicados
De cada grupo de copias idénticas se conserva siempre la más vieja. En la tarjeta ves cuántas copias están en la **misma carpeta** que el original y cuántas en **otra carpeta**. En DETALLE cada original aparece como "✔ se conserva: …" con sus copias abajo, y cada copia dice **MISMA carpeta** u **OTRA carpeta**. Elegir un grupo marca solo sus copias, nunca el original.

## DETALLE

Las listas completas:
- **RECOMENDADO**: lo mismo que las tarjetas, ítem por ítem.
- **ARCHIVOS PESADOS**: los 100 archivos más grandes. Los de programas o sistema aparecen en gris.
- **CARPETAS**: qué carpeta ocupa más, con el porcentaje y sus subcarpetas.

Seleccionás filas (Ctrl o Shift para varias) y elegís:
- **→ GUARDA MANUAL**: las carga en GUARDA MANUAL con ruta completa y el filtro en *todo*, así un proyecto no queda mitad en la PC y mitad en el HDD.
- **✖ BORRAR SELECCIÓN**: borra para siempre, con confirmación y log en `C:\Users\<vos>\LimpiaGuarda_logs\`.
- **ABRIR UBICACIÓN**: abre el Explorador con el archivo marcado.

## MAPA

Bloques tipo WizTree: cada rectángulo es del tamaño de lo que ocupa.
- Pasá el mouse para ver ruta y tamaño. **Clic** selecciona, y los botones de abajo funcionan sobre eso.
- **Doble clic** entra a la carpeta. **Clic derecho** o ▲ SUBIR vuelve.
- Los colores marcan el tipo: media/RAW, proyectos DAW, instaladores/comprimidos y otros. Lo de menos de 512 KB se junta en "otros".

## Lo que el análisis NUNCA recomienda
- Nada dentro de `AppData`, `Program Files`, `Windows`, `.git` o `site-packages`. Cuenta para el tamaño, pero no se toca, porque romperías programas.
- Copias de samples dentro de proyectos de DAW.
- Archivos de OneDrive "solo en la nube": no ocupan disco y no se tocan.
- Symlinks y junctions: no se siguen, así no cuenta nada dos veces.

Además, la app se niega a borrar la raíz de un disco, tu carpeta de usuario, Escritorio, Documentos, Descargas (la carpeta en sí) y carpetas del sistema.

## GUARDA MANUAL

Para elegir a mano qué mandar al HDD:
1. **ELEGIR…**: el HDD externo (o una carpeta adentro, tipo `E:\Guardado`). Es el mismo que muestra INICIO.
2. Agregá carpetas o archivos, o mandalos desde DETALLE o MAPA.
3. **FILTRO**: *todo / +1 mes / +3 / +6 meses / +1 / +2 años*. Solo se procesan archivos cuya **última modificación** es más vieja que eso. Lo reciente se queda en la PC.
4. **Guardar con ruta completa**: en el HDD queda `E:\Guardado\C\Users\Teska\Musica\...`. Sirve para saber siempre de dónde vino cada cosa.
5. Modo **MOVER AL HDD** (libera espacio) o **SOLO COPIA**, y ▶ EJECUTAR.

### Reglas de seguridad al guardar
- Un original se borra **solo** si el SHA-256 de la copia, releída desde el disco, coincide con el original.
- Si el archivo cambió mientras se copiaba (por ejemplo, un proyecto abierto en el DAW), no se borra.
- Copia primero a `.lgpart` y recién renombra cuando verificó.
- Una copia idéntica que ya está en el HDD no se recopia. Un archivo distinto con el mismo nombre no se pisa: se guarda como `nombre (guardado 2).ext`.
- Avisa si el origen está en OneDrive (borrarlo localmente lo borra de la nube) o si está en el mismo disco que el destino.
- Cada ejecución deja un log-recibo en `<destino>\_limpiaguarda_logs\`.

## AUTO

Dos tareas pueden correr solas con el **Programador de tareas de Windows**, aunque la app esté cerrada. No abren ninguna ventana.
- **Limpieza semanal**: lo mismo que LIMPIAR con las tarjetas SEGURO.
- **Sesiones viejas**: lo mismo que GUARDAR con las tarjetas → HDD.

1. Elegí cuándo (*cada domingo…* o *el día 1 de cada mes*) y la hora.
2. **PROGRAMAR**: te muestra exactamente qué va a hacer sin preguntar y lo registra en la carpeta `LimpiaGuarda` del Programador. Usa la CARPETA y el HDD que tengas elegidos en ese momento.
3. **PROBAR AHORA** lo corre ya mismo, igual que Windows. **QUITAR** lo saca del Programador.

Si la PC estaba apagada a esa hora, la tarea corre apenas se prende. Cada corrida deja una línea en `C:\Users\<vos>\LimpiaGuarda_logs\programado.log`, además del log de borrado o del HDD. Esa línea se ve en ÚLTIMAS CORRIDAS y al abrir la app.

### Reglas del modo automático
- **Limpieza**: solo lo 🟢 SEGURO, y **nunca vacía la papelera**, porque es tu red de seguridad. Vaciarla queda solo para LIMPIAR en INICIO, que pide confirmación. Las caches usadas en los últimos 2 días se saltean, por si VS Code, Cursor o un `next dev` están abiertos.
- **Sesiones viejas**: si el HDD no está conectado, no hace nada. Se saltea cualquier elemento que dé un aviso (OneDrive o mismo disco que el destino). Valen las mismas reglas de SHA-256 de siempre.
- Nunca busca duplicados ni toca lo de REVISAR.

También se puede correr a mano desde una terminal:
```
python limpia_guarda.py --preset limpieza --root C:\Users\Teska
python limpia_guarda.py --preset sesiones --root C:\Users\Teska --dest E:\Guardado
```

## LOG

Todo lo que pasó en la sesión. La última línea se ve siempre abajo de la ventana; con un clic sobre ella venís acá.

⚠ Los borrados son definitivos y no pasan por la papelera, porque si no, no liberarían espacio.

## Si venís de Teska Vault
La primera vez que abras Limpia&Guarda, la app trae sola lo anterior:
- La config `~\.teska_vault.json` se **copia** a `~\.limpia_guarda.json`. La vieja queda como respaldo.
- La carpeta `~\TeskaVault_logs\` se **renombra** a `~\LimpiaGuarda_logs\`, con todos tus logs adentro.
- En el HDD, los logs viejos (`_vault_logs`) quedan donde están y el botón LOGS los sigue abriendo. Las copias viejas `nombre (vault 2).ext` se reconocen y no se vuelven a copiar.
- Las tareas programadas se registran en la carpeta `LimpiaGuarda` del Programador. Si tenías alguna en `TeskaVault`, borrala desde el Programador y volvé a programarla en AUTO.

El archivo para abrir la app no lleva `&` en el nombre (`Abrir LimpiaGuarda.bat`) porque Windows corta los `.bat` en el `&`.

## Ideas v0.5
- Presets editables (qué categorías, cuántos días).
- Aviso de Windows (toast) al terminar una corrida automática.
- Resaltar en el mapa lo que está recomendado.
