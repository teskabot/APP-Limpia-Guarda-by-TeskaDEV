# ▓▒░ Limpia&Guarda v1.0.0 · by Teskadev

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

Para publicar una versión: cambiá `VERSION` en `limpia_guarda.py`, hacé commit y subí un tag con ese número (`git tag v1.0.0` y `git push origin v1.0.0`). Se crea un **Release** con los tres instaladores y las instrucciones de qué bajar.

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

## La interfaz (v1.0)

Cuatro pestañas: **limpiar**, **guardar en hdd**, **explorar** y **auto**. Las que hacen algo funcionan como un asistente: una pregunta por pantalla, un botón principal y arriba una barrita que dice en qué paso estás. Lo avanzado no desaparece, queda a un clic ("opciones", "ver", explorar).

Colores: **verde** es lo principal (botones, pestaña activa, limpiar y listo), **naranja** son los números y los avisos, **celeste** es todo lo del HDD y **rojo** es borrar o error.

- Arriba a la derecha está siempre lo **liberado** en total y el **hdd** elegido (clic para cambiarlo).
- El **log** completo está en el link chiquito a la derecha de las pestañas, o haciendo clic en la última línea de abajo.
- Atajos: **Enter** sigue al próximo paso · **Esc** vuelve un paso (o cancela lo que está corriendo) · **F5** analiza · **Ctrl+1…4** cambia de pestaña (Cmd en Mac).

## limpiar

1. **¿Qué querés revisar?** Por defecto tu usuario; también una carpeta o un disco entero (clic en la ruta, o arrastrá una carpeta encima). **analizar** mira todo **sin tocar nada**.
2. **Encontré X para borrar.** Una línea por categoría, con su tamaño. Ya viene marcado lo seguro; un clic en una línea la saca o la suma, y tu elección se recuerda. **ver** te lleva a la lista completa en explorar.
   - Marcadas de entrada: papelera, temporales con más de 1 día, caches de npm/pip/Python/Next/Vite/VS Code/Cursor, `node_modules`/`.venv` de proyectos quietos hace +30 días (solo si tienen `package.json`/`requirements`, así se reinstalan con un comando), instaladores e ISOs viejos en Descargas.
   - Desmarcadas de entrada (dicen *revisar*): ZIP/RAR viejos en Descargas y duplicados exactos (mismo SHA-256).
   - Las caches usadas en los últimos 2 días nunca se tocan, porque pueden estar abiertas.
3. **¿Borro X para siempre?** Muestra qué se borra y cualquier aviso. **sí, limpiar** y al final ves "✔ Liberaste X".

### Duplicados
De cada grupo de copias idénticas se conserva siempre la más vieja. En explorar → recomendado cada original aparece como "✔ se conserva: …" con sus copias abajo, y cada copia dice **MISMA carpeta** u **OTRA carpeta**. Elegir un grupo marca solo sus copias, nunca el original.

## guardar en hdd

1. **¿Dónde está tu disco externo?** El HDD, o una carpeta adentro (tipo `E:\Guardado`). Se recuerda.
2. **¿Qué mando al HDD?** Las sugerencias del análisis (proyectos de DAW/video sin tocar hace +6 meses, **como carpeta entera** para no romper los samples, y WAV/video/RAW de 20 MB o más sin tocar hace +6 meses) más lo que sumes con **+ carpeta**, **+ archivos** o arrastrando.
3. **¿Muevo X al HDD?** **mover y verificar**. En **opciones** elegís *mover* o *solo copiar*, el filtro por antigüedad (solo para lo elegido a mano) y si se guarda con la ruta completa.

## explorar

Para mirar a fondo. Arriba elegís la vista:
- **mapa**: bloques tipo WizTree, cada rectángulo es del tamaño de lo que ocupa. Clic selecciona, doble clic entra a la carpeta, clic derecho o ▲ SUBIR vuelve. Los colores marcan el tipo (media/RAW, proyectos DAW, instaladores/comprimidos, otros). Lo de menos de 512 KB se junta en "otros".
- **recomendado**: lo mismo que limpiar y guardar, ítem por ítem.
- **archivos pesados**: los 100 archivos más grandes. Los de programas o sistema aparecen en gris.
- **carpetas**: qué carpeta ocupa más, con el porcentaje y sus subcarpetas.

Seleccionás (Ctrl o Shift para varias) y elegís:
- **guardar en hdd**: lo suma al paso 2 de guardar en hdd, con ruta completa y el filtro en *todo*, así un proyecto no queda mitad en la PC y mitad en el HDD.
- **borrar**: borra para siempre, con confirmación y log en `C:\Users\<vos>\LimpiaGuarda_logs\`.
- **abrir ubicación**: abre el Explorador con el archivo marcado.

## Lo que el análisis NUNCA recomienda
- Nada dentro de `AppData`, `Program Files`, `Windows`, `.git` o `site-packages`. Cuenta para el tamaño, pero no se toca, porque romperías programas.
- Copias de samples dentro de proyectos de DAW.
- Archivos de OneDrive "solo en la nube": no ocupan disco y no se tocan.
- Symlinks y junctions: no se siguen, así no cuenta nada dos veces.

Además, la app se niega a borrar la raíz de un disco, tu carpeta de usuario, Escritorio, Documentos, Descargas (la carpeta en sí) y carpetas del sistema.

## Reglas de seguridad al guardar en el HDD
- Un original se borra **solo** si el SHA-256 de la copia, releída desde el disco, coincide con el original.
- Si el archivo cambió mientras se copiaba (por ejemplo, un proyecto abierto en el DAW), no se borra.
- Copia primero a `.lgpart` y recién renombra cuando verificó.
- Una copia idéntica que ya está en el HDD no se recopia. Un archivo distinto con el mismo nombre no se pisa: se guarda como `nombre (guardado 2).ext`.
- Avisa si el origen está en OneDrive (borrarlo localmente lo borra de la nube) o si está en el mismo disco que el destino.
- Cada ejecución deja un log-recibo en `<destino>\_limpiaguarda_logs\`.

## auto

Dos tareas pueden correr solas con el **Programador de tareas de Windows**, aunque la app esté cerrada. No abren ninguna ventana.
- **Limpieza semanal**: lo mismo que limpiar con lo marcado de entrada.
- **Sesiones viejas**: lo mismo que guardar en hdd con las sugerencias.

1. **¿Qué querés que haga solo?** Elegí la tarea; al lado ves si ya está programada.
2. **¿Cuándo?** (*cada domingo…* o *el día 1 de cada mes*) y la hora. Ahí mismo dice qué va a hacer sin preguntar. **programar** la registra en la carpeta `LimpiaGuarda` del Programador, con la carpeta y el HDD que tengas elegidos en ese momento.
3. Si ya estaba programada, en el paso 2 aparecen **probar ahora** (la corre ya, igual que Windows) y **quitar**.

Si la PC estaba apagada a esa hora, la tarea corre apenas se prende. Cada corrida deja una línea en `C:\Users\<vos>\LimpiaGuarda_logs\programado.log`, además del log de borrado o del HDD. Esa línea se ve en **últimas corridas** (paso 1 de auto) y al abrir la app.

### Reglas del modo automático
- **Limpieza**: solo lo seguro, y **nunca vacía la papelera**, porque es tu red de seguridad. Vaciarla queda solo para la pestaña limpiar, que pide confirmación. Las caches usadas en los últimos 2 días se saltean, por si VS Code, Cursor o un `next dev` están abiertos.
- **Sesiones viejas**: si el HDD no está conectado, no hace nada. Se saltea cualquier elemento que dé un aviso (OneDrive o mismo disco que el destino). Valen las mismas reglas de SHA-256 de siempre.
- Nunca busca duplicados ni toca lo de revisar.

También se puede correr a mano desde una terminal:
```
python limpia_guarda.py --preset limpieza --root C:\Users\Teska
python limpia_guarda.py --preset sesiones --root C:\Users\Teska --dest E:\Guardado
```

## log

Todo lo que pasó en la sesión. Se abre con el link **log** a la derecha de las pestañas. La última línea se ve siempre abajo de la ventana; con un clic sobre ella venís acá.

⚠ Los borrados son definitivos y no pasan por la papelera, porque si no, no liberarían espacio.

## Si venís de Teska Vault
La primera vez que abras Limpia&Guarda, la app trae sola lo anterior:
- La config `~\.teska_vault.json` se **copia** a `~\.limpia_guarda.json`. La vieja queda como respaldo.
- La carpeta `~\TeskaVault_logs\` se **renombra** a `~\LimpiaGuarda_logs\`, con todos tus logs adentro.
- En el HDD, los logs viejos (`_vault_logs`) quedan donde están y el botón LOGS los sigue abriendo. Las copias viejas `nombre (vault 2).ext` se reconocen y no se vuelven a copiar.
- Las tareas programadas se registran en la carpeta `LimpiaGuarda` del Programador. Si tenías alguna en `TeskaVault`, borrala desde el Programador y volvé a programarla en AUTO.

El archivo para abrir la app no lleva `&` en el nombre (`Abrir LimpiaGuarda.bat`) porque Windows corta los `.bat` en el `&`.

## Ideas para después de la 1.0
- Presets editables (qué categorías, cuántos días).
- Aviso de Windows (toast) al terminar una corrida automática.
- Resaltar en el mapa lo que está recomendado.
