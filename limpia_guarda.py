#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Limpia&Guarda · by Teskadev
1) ANALIZA qué ocupa espacio y recomienda qué borrar o mandar al Vault.
2) VAULT: copia al disco externo, verifica byte a byte (SHA-256) y recién ahí borra.
"Construyo lo que ofrezco."
"""
from __future__ import annotations

import hashlib
import heapq
import json
import os
import queue
import shutil
import stat
import subprocess
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except ImportError:  # permite usar el motor sin GUI (tests, scripts)
    tk = None

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

APP_NAME = "Limpia&Guarda"
APP_BY = "by Teskadev"
VERSION = "0.4"
CHUNK = 8 * 1024 * 1024
TMP_SUFFIX = ".lgpart"
LOG_DIR = "_limpiaguarda_logs"
CONFIG_PATH = Path.home() / ".limpia_guarda.json"
DELETE_LOG_DIR = Path.home() / "LimpiaGuarda_logs"
AUTO_LOG = DELETE_LOG_DIR / "programado.log"
TASK_FOLDER = "LimpiaGuarda"
# nombres de cuando la app se llamaba Teska Vault: se siguen reconociendo y se migran
LEGACY_TMP_SUFFIX = ".vaultpart"
LEGACY_LOG_DIR = "_vault_logs"
LEGACY_CONFIG_PATH = Path.home() / ".teska_vault.json"
LEGACY_DELETE_LOG_DIR = Path.home() / "TeskaVault_logs"
MODE_BACKUP = "backup"
MODE_MOVE = "move"
DAY = 86400
SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information", LOG_DIR, LEGACY_LOG_DIR, "$Recycle.Bin",
             # carpetas internas de macOS (también aparecen en discos externos usados en una Mac)
             ".Trash", ".Trashes", ".Spotlight-V100", ".fseventsd", ".DocumentRevisions-V100", ".TemporaryItems"}
# Mac: rutas que no se recorren al analizar "/" (otros discos, el volumen de datos repetido, dispositivos)
SKIP_ABS = {"/System/Volumes", "/Volumes", "/dev", "/net", "/home", "/cores"} if IS_MAC else set()
MAC_TRASH = Path.home() / ".Trash"
AGE_OPTIONS = [("todo", 0), ("+1 mes", 30), ("+3 meses", 90), ("+6 meses", 180), ("+1 año", 365), ("+2 años", 730)]
BIN_PATH = "::papelera::"

# atributos de archivo de Windows
FILE_ATTRIBUTE_REPARSE_POINT = 0x400        # junctions / symlinks
CLOUD_MASK = 0x1000 | 0x40000 | 0x400000     # offline / recall (OneDrive "solo en la nube")
SF_DATALESS = 0x40000000                     # Mac: archivo de iCloud/OneDrive que está solo en la nube


class Cancelled(Exception):
    pass


class VerifyError(Exception):
    pass


# ───────────────────────────── utilidades ─────────────────────────────

def human(n: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while abs(n) >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.0f} {units[i]}" if i == 0 else f"{n:.2f} {units[i]}"


def age_txt(mtime: float, now: float | None = None) -> str:
    d = max(0.0, ((now or time.time()) - mtime) / DAY)
    if d < 1:
        return "hoy"
    if d < 30:
        return f"{int(d)} día{'s' if int(d) != 1 else ''}"
    if d < 365:
        m = int(d // 30)
        return f"{m} mes{'es' if m != 1 else ''}"
    a = d / 365
    return f"{a:.1f} años".replace(".0 ", " ")


def hash_file(path, cancel=None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            h.update(chunk)
    return h.hexdigest()


def partial_hash(path, size, part=64 * 1024) -> str:
    """Hash rápido: principio + final del archivo. Filtra candidatos a duplicado."""
    h = hashlib.sha256(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(part))
        if size > 2 * part:
            f.seek(-part, os.SEEK_END)
            h.update(f.read(part))
    return h.hexdigest()


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def is_inside(child, parent) -> bool:
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def same_device(a, b) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def _base_name(src: Path) -> str:
    if src.name:
        return src.name
    d = src.drive.strip(":\\/") or "raiz"
    return f"disco_{d}"


def vault_base(src: Path, full_path: bool) -> Path:
    """Dónde cae un origen dentro del Vault: solo su nombre, o la ruta completa (C/Users/...)."""
    if not full_path:
        return Path(_base_name(src))
    a = Path(os.path.abspath(src))
    drive = (a.drive.strip(":\\/") or "raiz").replace("\\", "_").replace("/", "_")
    return Path(drive, *a.parts[1:])


def _sysdirs():
    if IS_MAC:
        return ["/System", "/Library", "/Applications", "/usr", "/bin", "/sbin", "/private", "/opt"]
    return [os.environ.get(k) for k in ("WINDIR", "SYSTEMROOT", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA")]


def cloud_dirs():
    """Carpetas sincronizadas con la nube: borrar ahí borra también en la nube."""
    if IS_MAC:
        h = Path.home()
        return [str(h / "Library" / "Mobile Documents"), str(h / "Library" / "CloudStorage")]
    return [os.environ.get(k) for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")]


def volume_of(p) -> str:
    """El disco donde está p: 'C:' en Windows, el punto de montaje ('/' o '/Volumes/HDD') en Mac/Linux."""
    a = os.path.abspath(str(p))
    if IS_WIN:
        return os.path.splitdrive(a)[0].upper() or os.sep
    while not os.path.ismount(a):
        parent = os.path.dirname(a)
        if parent == a:
            break
        a = parent
    return a


def volume_label(vol) -> str:
    if IS_WIN:
        return vol + "\\"
    if vol == os.sep:
        return "Macintosh HD" if IS_MAC else "/"
    return os.path.basename(vol)


def is_disk_root(p) -> bool:
    a = os.path.abspath(str(p))
    return Path(a).parent == Path(a) or os.path.ismount(a)


# ───────────────────────────── motor: VAULT ─────────────────────────────

def scan(sources, cancel=None, min_age_days=0, full_path=False, now=None):
    """
    Devuelve (archivos[(src, rel, size)], carpetas[rel], total_bytes, errores[(ruta, msg)], salteados_recientes).
    Con min_age_days > 0 solo entran archivos cuya última modificación es más vieja que eso.
    """
    files, dirs, errors, total, recent = [], [], [], 0, 0
    cutoff = (now or time.time()) - min_age_days * DAY if min_age_days else None

    def onerror(e):
        errors.append((getattr(e, "filename", "?"), str(e)))

    for s in sources:
        s = Path(s)
        if not s.exists():
            errors.append((str(s), "no existe"))
            continue
        base = vault_base(s, full_path)
        entries = []
        if s.is_file():
            entries.append((s, base))
        else:
            for root, dnames, fnames in os.walk(s, onerror=onerror):
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                r = Path(root)
                dnames[:] = [d for d in dnames
                             if d not in SKIP_DIRS and not os.path.islink(os.path.join(root, d))]
                if cutoff is None:  # sin filtro replico también carpetas vacías
                    dirs.append(base / r.relative_to(s))
                for n in fnames:
                    p = r / n
                    if n.endswith((TMP_SUFFIX, LEGACY_TMP_SUFFIX)) or p.is_symlink():
                        continue
                    entries.append((p, base / p.relative_to(s)))
        for p, rel in entries:
            try:
                st = p.stat()
            except OSError as e:
                errors.append((str(p), str(e)))
                continue
            if cutoff is not None and st.st_mtime > cutoff:
                recent += 1
                continue
            files.append((p, rel, st.st_size))
            total += st.st_size
    return files, dirs, total, errors, recent


def preflight(sources, dest, mode):
    """Chequeos antes de tocar nada. Devuelve (errores, avisos)."""
    errors, warnings = [], []
    if not dest:
        return ["Elegí el disco o carpeta destino."], []
    d = Path(dest)
    if not d.is_dir():
        return [f"El destino no existe o no está conectado:\n{dest}"], []
    if not sources:
        return ["Agregá al menos una carpeta o archivo de origen."], []
    onedrive = cloud_dirs()
    cloud_name = "iCloud/la nube" if IS_MAC else "OneDrive"
    for s in sources:
        sp = Path(s)
        if is_inside(d, sp):
            errors.append(f"El destino está adentro del origen:\n{s}")
        elif is_inside(sp, d):
            errors.append(f"Ese origen ya está adentro del destino en el HDD:\n{s}")
        if mode == MODE_MOVE:
            if sp.is_dir() and is_disk_root(sp):
                errors.append(f"No muevo un disco entero ({s}). Elegí carpetas.")
            if any(sd and is_inside(sp, sd) for sd in _sysdirs()):
                errors.append(f"Eso es una carpeta del sistema, no la muevo:\n{s}")
            if same_device(sp, d):
                warnings.append(f"Está en el MISMO disco que el destino, moverlo no libera espacio:\n{s}")
            if any(o and is_inside(sp, o) for o in onedrive):
                warnings.append(f"Está dentro de {cloud_name}: borrarlo acá lo borra también de la nube:\n{s}")
    return errors, warnings


def remove_file(p):
    try:
        os.remove(p)
    except PermissionError:
        os.chmod(p, stat.S_IWRITE)  # archivos de solo lectura en Windows
        os.remove(p)


def _variants(dst: Path):
    yield dst
    i = 2
    while True:
        yield dst.with_name(f"{dst.stem} (guardado {i}){dst.suffix}")
        i += 1


def _legacy_variants(dst: Path):
    """Nombres que usaba Teska Vault: 'nombre (vault 2).ext'. Solo para reconocer copias ya hechas."""
    i = 2
    while True:
        cand = dst.with_name(f"{dst.stem} (vault {i}){dst.suffix}")
        if not cand.exists():
            return
        yield cand
        i += 1


def copy_and_verify(src: Path, dst: Path, cancel, on_bytes=lambda n: None):
    """
    Copia src -> dst y verifica SHA-256 releyendo la copia del disco.
    Si ya existe una copia idéntica, no copia nada. Si existe una distinta,
    usa 'nombre (guardado 2).ext' en vez de pisarla.
    Devuelve (estado, destino_final, sha256). estado = 'copied' | 'already'.
    """
    size = src.stat().st_size
    src_hash = None
    target = None
    for cand in _legacy_variants(dst):
        if cand.is_file() and cand.stat().st_size == size:
            if src_hash is None:
                src_hash = hash_file(src, cancel)
            if hash_file(cand, cancel) == src_hash:
                on_bytes(size)
                return "already", cand, src_hash
    for cand in _variants(dst):
        if not cand.exists():
            target = cand
            break
        if cand.is_file() and cand.stat().st_size == size:
            if src_hash is None:
                src_hash = hash_file(src, cancel)
            if hash_file(cand, cancel) == src_hash:
                on_bytes(size)
                return "already", cand, src_hash

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + TMP_SUFFIX)
    h = hashlib.sha256()
    try:
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            while True:
                chunk = fi.read(CHUNK)
                if not chunk:
                    break
                if cancel.is_set():
                    raise Cancelled()
                h.update(chunk)
                fo.write(chunk)
                on_bytes(len(chunk))
            fo.flush()
            os.fsync(fo.fileno())
        src_hash = h.hexdigest()
        if hash_file(tmp, cancel) != src_hash:
            raise VerifyError("la copia no coincide con el original")
        shutil.copystat(src, tmp)
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return "copied", target, src_hash


def prune_empty_dirs(sources):
    for s in sources:
        s = Path(s)
        if not s.is_dir():
            continue
        for root, _dirs, files in os.walk(s, topdown=False):
            try:
                if files == [".DS_Store"]:  # Mac: el Finder deja este archivo en cada carpeta que abrís
                    os.remove(os.path.join(root, ".DS_Store"))
                os.rmdir(root)
            except OSError:
                pass  # no está vacía: quedó algo sin mover, se queda


def run_job(sources, dest, mode, cancel, emit=None, min_age_days=0, full_path=False):
    """Motor principal del Vault. emit(tipo, *datos) informa a la GUI."""
    emit = emit or (lambda *a: None)
    dest = Path(dest)
    stats = dict(total_files=0, total_bytes=0, done_bytes=0, copied=0, already=0,
                 deleted=0, freed=0, skipped=0, errors=0, recent=0,
                 cancelled=False, aborted=False, log_path="")
    try:
        emit("log", "info", f"Escaneando {len(sources)} origen(es)…")
        files, dirs, total, scan_errors, recent = scan(sources, cancel, min_age_days, full_path)
        stats.update(total_files=len(files), total_bytes=total, recent=recent)
        msg = f"{len(files)} archivos · {human(total)}"
        if min_age_days:
            msg += f" · filtro +{min_age_days} días: salteo {recent} recientes"
        emit("log", "info", msg)

        needed = 0
        for _src, rel, size in files:
            d = dest / rel
            try:
                if d.is_file() and d.stat().st_size == size:
                    continue
            except OSError:
                pass
            needed += size
        free = shutil.disk_usage(dest).free
        if needed > free:
            emit("log", "err", f"No entra: hacen falta {human(needed)} y el destino tiene {human(free)} libres.")
            stats["aborted"] = True
            return stats

        log_dir = dest / LOG_DIR
        log_dir.mkdir(exist_ok=True)
        log_path = log_dir / f"{datetime.now():%Y-%m-%d_%H%M%S}_{mode}.log"
        stats["log_path"] = str(log_path)

        for rel in dirs:  # replica también las carpetas vacías
            (dest / rel).mkdir(parents=True, exist_ok=True)

        with open(log_path, "w", encoding="utf-8") as log:
            log.write(f"# {APP_NAME} {VERSION} · modo {mode} · filtro {min_age_days} días · "
                      f"{datetime.now().isoformat(timespec='seconds')}\n")
            log.write(f"# destino: {dest}\n# estado\tsha256\torigen\tdestino\n")

            def record(status, src, dst="", digest=""):
                log.write(f"{status}\t{digest}\t{src}\t{dst}\n")
                log.flush()

            for path, err in scan_errors:
                stats["errors"] += 1
                emit("log", "err", f"No pude leer {path}: {err}")
                record("ERROR_LECTURA", path, "", err)

            t0 = time.time()
            last = 0.0

            def on_bytes(n):
                nonlocal last
                stats["done_bytes"] += n
                now = time.time()
                if now - last > 0.15:
                    last = now
                    emit("progress", dict(stats), now - t0)

            for i, (src, rel, size) in enumerate(files, 1):
                if cancel.is_set():
                    raise Cancelled()
                base_done = stats["done_bytes"]
                emit("current", f"[{i}/{len(files)}] {rel}")
                try:
                    before = src.stat()
                    status, final, digest = copy_and_verify(src, dest / rel, cancel, on_bytes)
                    if status == "already":
                        stats["already"] += 1
                        record("YA_ESTABA", src, final, digest)
                    else:
                        stats["copied"] += 1
                        record("COPIADO_OK", src, final, digest)
                        if final != dest / rel:
                            emit("log", "warn", f"Ya había otro '{rel.name}' distinto, guardado como '{final.name}'")
                    if mode == MODE_MOVE:
                        after = src.stat()
                        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                            stats["skipped"] += 1
                            emit("log", "warn", f"Cambió mientras lo copiaba, NO lo borro: {src}")
                            record("CAMBIO_NO_BORRADO", src, final, digest)
                        else:
                            remove_file(src)
                            stats["deleted"] += 1
                            stats["freed"] += size
                            record("BORRADO", src, final, digest)
                except Cancelled:
                    raise
                except VerifyError as e:
                    stats["errors"] += 1
                    emit("log", "err", f"FALLÓ LA VERIFICACIÓN, original intacto: {src} ({e})")
                    record("VERIFICACION_FALLIDA", src, dest / rel)
                except Exception as e:  # noqa: BLE001
                    stats["errors"] += 1
                    emit("log", "err", f"Error con {src}: {e}")
                    record("ERROR", src, dest / rel, str(e))
                finally:
                    stats["done_bytes"] = base_done + size
                emit("progress", dict(stats), time.time() - t0)

            if mode == MODE_MOVE:
                prune_empty_dirs(sources)
    except Cancelled:
        stats["cancelled"] = True
        emit("log", "warn", "Cancelado. Lo que ya estaba verificado quedó bien; el resto no se tocó.")
    except Exception as e:  # noqa: BLE001
        stats["errors"] += 1
        emit("log", "err", f"Error general: {e}")
    finally:
        emit("done", dict(stats))
    return stats


# ───────────────────────────── motor: ANÁLISIS ─────────────────────────────

# proyectos de audio / video: la carpeta que los contiene se trata como una unidad
DAW_EXT = {".als", ".flp", ".rpp", ".ptx", ".ptf", ".cpr", ".song", ".bwproject", ".reason", ".aup3", ".aup",
           ".sesx", ".npr", ".dawproject", ".prproj", ".aep", ".drp", ".veg",
           ".logicx", ".band", ".fcpbundle", ".imovielibrary"}  # Mac: Logic, GarageBand, Final Cut, iMovie
MEDIA_EXT = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a", ".ogg",
             ".mp4", ".mov", ".mkv", ".avi", ".mxf", ".m4v", ".webm", ".mts", ".m2ts", ".braw", ".r3d",
             ".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".orf", ".rw2", ".psd", ".psb", ".tif", ".tiff"}
INSTALLER_EXT = {".exe", ".msi", ".msix", ".msixbundle", ".appx", ".dmg", ".pkg", ".iso", ".img"}
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".xz", ".bz2"}

# caches de proyectos: se borran sin culpa, se regeneran solas
CACHE_DIRS = {"__pycache__": "cache de Python, se regenera sola",
              ".pytest_cache": "cache de pytest", ".mypy_cache": "cache de mypy", ".ruff_cache": "cache de ruff",
              ".parcel-cache": "cache de Parcel", ".next": "build de Next.js (npm run build/dev)",
              ".nuxt": "build de Nuxt", ".turbo": "cache de Turborepo", ".svelte-kit": "build de SvelteKit",
              ".angular": "cache de Angular", ".vite": "cache de Vite"}
# dependencias: solo si el proyecto tiene manifiesto (o sea, se reinstalan) y está quieto
DEP_DIRS = {"node_modules": (("package.json",), "npm install"),
            ".venv": (("requirements.txt", "pyproject.toml", "Pipfile", "setup.py"), "pip install -r requirements.txt"),
            "venv": (("requirements.txt", "pyproject.toml", "Pipfile", "setup.py"), "pip install -r requirements.txt")}
# se cuentan para el tamaño pero NUNCA se recomiendan (romperías programas)
COUNT_ONLY_DIRS = {"AppData", "Program Files", "Program Files (x86)", "ProgramData", "Windows", ".git",
                   "site-packages", "Library", ".vscode", ".cursor", "$WinREAgent", "Windows.old",
                   "Applications", "System"}
# Mac: "paquetes" que el Finder muestra como un solo archivo. Sacarles algo de adentro los rompe
# (por ejemplo, la biblioteca de Fotos), así que se cuentan pero nunca se recomiendan.
COUNT_ONLY_EXT = {".app", ".photoslibrary", ".photolibrary", ".musiclibrary", ".tvlibrary", ".aplibrary",
                  ".migratedphotolibrary", ".framework", ".bundle", ".plugin", ".component", ".vst", ".vst3",
                  ".kext", ".xcarchive"}
BIN_NAME = "Papelera" if not IS_WIN else "Papelera de reciclaje"

REC_DEFS = [
    ("bin", BIN_NAME, "seguro", "empty_bin",
     "Ya lo tiraste, pero sigue ocupando lugar hasta que la vacíes."),
    ("temp", "Archivos temporales (+1 día)", "seguro", "delete",
     "Restos que dejan instaladores y programas. Lo que esté en uso no se puede borrar y se saltea solo."),
    ("cache", "Caches de desarrollo", "seguro", "delete",
     "Caches de npm/pip/Python/frameworks. Se regeneran solas la próxima vez que las necesites "
     "(cerrá Cursor/VS Code antes si están en la lista)."),
    ("deps", "Dependencias de proyectos quietos (+30 días)", "seguro", "delete",
     "node_modules y entornos virtuales de proyectos que no tocás hace rato. Tienen package.json / "
     "requirements, así que se reinstalan con un comando cuando vuelvas al proyecto."),
    ("installers", "Instaladores e ISOs en Descargas (+30 días)", "seguro", "delete",
     "El programa ya está instalado (o ya fue). Si algún día lo necesitás, se baja de nuevo."),
    ("archives", "Comprimidos viejos en Descargas (+30 días)", "revisar", "delete",
     "ZIP/RAR que probablemente ya descomprimiste. Revisá que no sea lo único que tenés de eso."),
    ("dups", "Duplicados exactos (mismo SHA-256)", "revisar", "delete",
     "Copias idénticas byte a byte. Se conserva siempre una (la más vieja) y nunca se proponen copias "
     "que estén dentro de proyectos de DAW/video, para no romper referencias de samples."),
    ("projects", "Proyectos de audio/video sin tocar (+6 meses)", "vault", "vault",
     "Carpetas con .als/.flp/.rpp/.prproj/etc. que no cambiaron hace más de 6 meses. Guardalas ENTERAS en "
     "el HDD así no se rompen las referencias a samples y renders."),
    ("media", "Media pesada sin tocar (+6 meses)", "vault", "vault",
     "WAV, video y RAW de 20 MB o más, fuera de proyectos. Al HDD: libera espacio sin perder nada."),
]
BADGE = {"seguro": "[SEGURO] ", "revisar": "[REVISAR]", "vault": "[→ HDD]  "}


@dataclass
class Item:
    path: str
    size: int
    note: str = ""
    mtime: float = 0.0   # último uso conocido (para las caches)
    ref: str = ""        # duplicados: la copia que se conserva


def same_folder(a, b) -> bool:
    return _norm(os.path.dirname(a)) == _norm(os.path.dirname(b))


def dup_where(path, ref) -> str:
    """Dónde está el duplicado respecto del original que se conserva."""
    if same_folder(path, ref):
        return f"misma carpeta que el original «{os.path.basename(ref)}»"
    return f"original en otra carpeta: {ref}"


class MapNode:
    """Bloque del mapa visual. kind: dir | file | rest (lo chico, junto)."""
    __slots__ = ("path", "name", "size", "kind", "kids")

    def __init__(self, path, name, size, kind, kids=None):
        self.path, self.name, self.size, self.kind = path, name, size, kind
        self.kids = kids or []


def prune_tree(node, gone) -> int:
    """Saca del mapa las rutas borradas (gone = rutas normalizadas). Devuelve los bytes que sacó."""
    if node is None or not node.kids:
        return 0
    removed, keep = 0, []
    for k in node.kids:
        if k.kind != "rest" and k.path and _norm(k.path) in gone:
            removed += k.size
            continue
        removed += prune_tree(k, gone)
        keep.append(k)
    node.kids = keep
    node.size = max(0, node.size - removed)
    return removed


@dataclass
class Rec:
    key: str
    title: str
    safety: str     # seguro | revisar | vault
    action: str     # delete | vault | empty_bin
    why: str
    items: list = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(i.size for i in self.items)


@dataclass
class Report:
    root: str
    total: int = 0
    n_files: int = 0
    n_dirs: int = 0
    cloud: int = 0
    errors: int = 0
    elapsed: float = 0.0
    top_files: list = field(default_factory=list)   # (size, mtime, path, es_de_sistema)
    top_dirs: list = field(default_factory=list)    # (path, size, [(path, size)])
    recs: list = field(default_factory=list)
    tree: MapNode | None = None                     # para el mapa visual
    created: float = 0.0

    def by_safety(self, safety) -> int:
        return sum(r.size for r in self.recs if r.safety == safety)


def default_temp_dirs():
    if IS_MAC:  # la carpeta temporal de tu usuario (/private/var/folders/…/T)
        t = os.environ.get("TMPDIR")
        return [os.path.realpath(t)] if t and os.path.isdir(t) else []
    if not IS_WIN:
        return []
    out = []
    for p in (os.environ.get("TEMP"), os.environ.get("TMP"),
              os.path.join(os.environ.get("LOCALAPPDATA", ""), "Temp")):
        if p and os.path.isdir(p) and _norm(p) not in {_norm(x) for x in out}:
            out.append(p)
    return out


def default_cache_dirs():
    out = []
    if sys.platform.startswith("win"):
        la, ra = os.environ.get("LOCALAPPDATA", ""), os.environ.get("APPDATA", "")
        cand = [(os.path.join(la, "npm-cache"), "cache de npm, se rearma sola"),
                (os.path.join(ra, "npm-cache"), "cache de npm, se rearma sola"),
                (os.path.join(la, "pip", "Cache"), "cache de pip, se rearma sola"),
                (os.path.join(la, "Yarn", "Cache"), "cache de Yarn, se rearma sola"),
                (os.path.join(ra, "Code", "Cache"), "cache de VS Code (cerralo antes)"),
                (os.path.join(ra, "Code", "CachedData"), "versiones viejas de VS Code (cerralo antes)"),
                (os.path.join(ra, "Cursor", "Cache"), "cache de Cursor (cerralo antes)"),
                (os.path.join(ra, "Cursor", "CachedData"), "versiones viejas de Cursor (cerralo antes)")]
    elif IS_MAC:
        h = Path.home()
        lc, sup = h / "Library" / "Caches", h / "Library" / "Application Support"
        cand = [(str(lc / "pip"), "cache de pip, se rearma sola"),
                (str(h / ".npm" / "_cacache"), "cache de npm, se rearma sola"),
                (str(lc / "Yarn"), "cache de Yarn, se rearma sola"),
                (str(lc / "Homebrew"), "descargas viejas de Homebrew, se rearma sola"),
                (str(sup / "Code" / "Cache"), "cache de VS Code (cerralo antes)"),
                (str(sup / "Code" / "CachedData"), "versiones viejas de VS Code (cerralo antes)"),
                (str(sup / "Cursor" / "Cache"), "cache de Cursor (cerralo antes)"),
                (str(sup / "Cursor" / "CachedData"), "versiones viejas de Cursor (cerralo antes)")]
    else:
        h = Path.home()
        cand = [(str(h / ".cache" / "pip"), "cache de pip"), (str(h / ".npm" / "_cacache"), "cache de npm")]
    return [(p, n) for p, n in cand if os.path.isdir(p)]


def _recycle_struct():
    import ctypes
    from ctypes import wintypes

    class SHQUERYRBINFO(ctypes.Structure):
        _pack_ = 1 if ctypes.sizeof(ctypes.c_void_p) == 4 else 8
        _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]
    return ctypes, SHQUERYRBINFO


def recycle_bin_info():
    """(bytes, cantidad) de la papelera, o None si no aplica."""
    if IS_MAC:  # sin "Acceso total al disco" macOS no deja leer la papelera: se saltea en silencio
        try:
            with os.scandir(MAC_TRASH) as it:
                entries = [e for e in it if e.name != ".DS_Store"]
            total = 0
            for e in entries:
                if e.is_dir(follow_symlinks=False):
                    for r, _d, fs in os.walk(e.path):
                        for f in fs:
                            try:
                                total += os.lstat(os.path.join(r, f)).st_size
                            except OSError:
                                pass
                else:
                    total += e.stat(follow_symlinks=False).st_size
            return total, len(entries)
        except OSError:
            return None
    if not IS_WIN:
        return None
    try:
        ctypes, S = _recycle_struct()
        info = S()
        info.cbSize = ctypes.sizeof(info)
        if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) == 0:
            return int(info.i64Size), int(info.i64NumItems)
    except Exception:  # noqa: BLE001
        pass
    return None


def empty_recycle_bin() -> bool:
    if IS_MAC:  # se lo pide al Finder (la primera vez macOS pregunta si la app lo puede controlar)
        try:
            r = subprocess.run(["osascript", "-e", 'tell application "Finder" to empty trash'],
                               capture_output=True, timeout=600)
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    if not IS_WIN:
        return False
    try:
        import ctypes
        ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)  # sin confirmación/UI/sonido
        return True
    except Exception:  # noqa: BLE001
        return False


class Analyzer:
    """Recorre una carpeta una sola vez y arma tops + recomendaciones."""

    def __init__(self, root, cancel=None, progress=None, now=None, downloads=None, temp_dirs=None,
                 cache_dirs=None, top_n=100, old_days=180, dep_days=30, dl_days=30,
                 dup_min=1 << 20, media_min=20 << 20, recycle=True, find_dups=True, map_tree=True,
                 map_min=512 << 10, map_k=40):
        self.root = os.path.abspath(str(root))
        self.find_dups, self.map_on = find_dups, map_tree
        self.map_min, self.map_k = map_min, map_k
        self.cancel = cancel or threading.Event()
        self.progress = progress or (lambda msg: None)
        self.now = now or time.time()
        self.old_cut = self.now - old_days * DAY
        self.dep_days, self.dl_days = dep_days, dl_days
        self.dup_min, self.media_min, self.top_n = dup_min, media_min, top_n
        self.recycle = recycle
        dl = downloads if downloads is not None else Path.home() / "Downloads"
        self.downloads = _norm(dl)
        self.special = {}  # carpetas con tratamiento especial: temporales y caches globales
        for t in (default_temp_dirs() if temp_dirs is None else temp_dirs):
            if os.path.isdir(t):
                self.special[_norm(t)] = ("temp", "", str(t))
        for c, note in (default_cache_dirs() if cache_dirs is None else cache_dirs):
            if os.path.isdir(c):
                self.special[_norm(c)] = ("cache", note, str(c))
        self.sys_dirs = {_norm(s) for s in _sysdirs() if s}
        self.recs = {k: Rec(k, t, s, a, w) for k, t, s, a, w in REC_DEFS}
        self.heap: list = []
        self.by_size = defaultdict(list)
        self.dir_sizes: dict = {}
        self.projects: list = []
        self.n_files = self.n_dirs = self.cloud = self.errors = 0
        self._seen_special: set = set()
        self._last = 0.0

    # ── recorrido
    def _tick(self, path, extra=""):
        t = time.time()
        if t - self._last > 0.25:
            self._last = t
            p = str(path)
            p = p if len(p) < 70 else "…" + p[-68:]
            self.progress(f"analizando… {self.n_files:,} archivos{extra} · {p}".replace(",", "."))

    def _file(self, path, name, size, mt, count_only, in_project, in_dl):
        entry = (size, path, mt, count_only)
        if len(self.heap) < self.top_n:
            heapq.heappush(self.heap, entry)
        elif size > self.heap[0][0]:
            heapq.heapreplace(self.heap, entry)
        if count_only:
            return
        ext = os.path.splitext(name)[1].lower()
        if size >= self.dup_min:
            self.by_size[size].append((path, mt, in_project))
        if in_dl and mt < self.now - self.dl_days * DAY:
            if ext in INSTALLER_EXT:
                self.recs["installers"].items.append(Item(path, size, f"bajado hace {age_txt(mt, self.now)}"))
            elif ext in ARCHIVE_EXT:
                self.recs["archives"].items.append(Item(path, size, f"bajado hace {age_txt(mt, self.now)}"))
        if not in_project and ext in MEDIA_EXT and size >= self.media_min and mt < self.old_cut:
            self.recs["media"].items.append(Item(path, size, f"sin tocar hace {age_txt(mt, self.now)}"))

    def _node(self, path, total, kids, small):
        """Arma el bloque de una carpeta: sus hijos más grandes y el resto junto en 'otros'."""
        name = os.path.basename(str(path).rstrip("\\/")) or str(path)
        if total < self.map_min or not kids:
            return MapNode(str(path), name, total, "dir")
        kids.sort(key=lambda k: -k.size)
        keep = [k for k in kids[:self.map_k] if k.size >= self.map_min]
        rest = small + sum(k.size for k in kids) - sum(k.size for k in keep)
        if rest > 0:
            keep.append(MapNode(None, "otros", rest, "rest"))
        return MapNode(str(path), name, total, "dir", keep)

    def walk(self, path, depth, count_only=False, in_project=False, in_dl=False, out=None):
        if self.cancel.is_set():
            raise Cancelled()
        self.n_dirs += 1
        self._tick(path)
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except OSError:
            self.errors += 1
            return 0, 0.0
        here_project = (not count_only and not in_project
                        and any(os.path.splitext(e.name)[1].lower() in DAW_EXT for e in entries))
        proj = in_project or here_project
        total, newest, subdirs = 0, 0.0, []
        kids = [] if out is not None else None   # bloques del mapa
        small = 0
        for e in entries:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                self.errors += 1
                continue
            attrs = getattr(st, "st_file_attributes", 0)
            if stat.S_ISLNK(st.st_mode) or attrs & FILE_ATTRIBUTE_REPARSE_POINT:
                continue  # symlinks y junctions: no se siguen (evita loops y contar doble)
            if stat.S_ISDIR(st.st_mode):
                subdirs.append((e, st))
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            self.n_files += 1
            if attrs & CLOUD_MASK or getattr(st, "st_flags", 0) & SF_DATALESS:
                self.cloud += 1  # OneDrive/iCloud "solo en la nube": no ocupa disco, no se toca
                continue
            total += st.st_size
            newest = max(newest, st.st_mtime)
            self._file(e.path, e.name, st.st_size, st.st_mtime, count_only, proj, in_dl)
            if kids is not None:
                if st.st_size >= self.map_min:
                    kids.append(MapNode(e.path, e.name, st.st_size, "file"))
                else:
                    small += st.st_size

        for e, st in subdirs:
            name, n = e.name, _norm(e.path)
            if name in SKIP_DIRS or (SKIP_ABS and e.path in SKIP_ABS):
                continue
            if n in self.special:
                s, nw = self._special(n, depth + 1, kids)
            elif not count_only and name in CACHE_DIRS:
                s, nw = self.walk(e.path, depth + 1, True, out=kids)
                if s:
                    self.recs["cache"].items.append(Item(e.path, s, CACHE_DIRS[name], nw))
            elif not count_only and name in DEP_DIRS:
                s, nw = self.walk(e.path, depth + 1, True, out=kids)
                self._dep(e.path, name, st, s)
            else:
                co = (count_only or name in COUNT_ONLY_DIRS or n in self.sys_dirs
                      or os.path.splitext(name)[1].lower() in COUNT_ONLY_EXT)
                s, nw = self.walk(e.path, depth + 1, co, proj, in_dl or n == self.downloads, out=kids)
            total += s
            newest = max(newest, nw)
            if depth + 1 <= 2:
                self.dir_sizes[e.path] = (s, depth + 1)

        if here_project and depth >= 1 and newest:
            self.projects.append((str(path), total, newest))
        if out is not None:
            out.append(self._node(path, total, kids, small))
        return total, newest

    def _special(self, n, depth, out=None):
        kind, note, orig = self.special[n]
        self._seen_special.add(n)
        if kind == "cache":
            s, nw = self.walk(orig, depth, True, out=out)
            if s:
                self.recs["cache"].items.append(Item(orig, s, note, nw))
            return s, nw
        total, newest = 0, 0.0  # temporales: cada elemento de primer nivel por separado
        kids = [] if out is not None else None
        small = 0
        try:
            with os.scandir(orig) as it:
                entries = list(it)
        except OSError:
            self.errors += 1
            return 0, 0.0
        for e in entries:
            try:
                st = e.stat(follow_symlinks=False)
                if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    s, nw = self.walk(e.path, depth + 1, True, out=kids)
                    nw = max(nw, st.st_mtime)
                elif stat.S_ISREG(st.st_mode):
                    self.n_files += 1
                    s, nw = st.st_size, st.st_mtime
                    if kids is not None:
                        if s >= self.map_min:
                            kids.append(MapNode(e.path, e.name, s, "file"))
                        else:
                            small += s
                else:
                    continue
            except OSError:
                continue
            total += s
            newest = max(newest, nw)
            if s and nw < self.now - DAY:
                self.recs["temp"].items.append(Item(e.path, s, f"último uso hace {age_txt(nw, self.now)}", nw))
        if out is not None:
            out.append(self._node(orig, total, kids, small))
        return total, newest

    def _dep(self, path, name, st, size):
        manifests, cmd = DEP_DIRS[name]
        parent = os.path.dirname(path)
        found = [os.path.join(parent, m) for m in manifests if os.path.exists(os.path.join(parent, m))]
        if not found or not size:
            return
        try:
            last = max([st.st_mtime] + [os.stat(f).st_mtime for f in found])
        except OSError:
            return
        if last < self.now - self.dep_days * DAY:
            self.recs["deps"].items.append(
                Item(path, size, f"se reinstala con «{cmd}» · proyecto quieto hace {age_txt(last, self.now)}"))

    # ── duplicados
    def _dups(self):
        groups = [(size, v) for size, v in self.by_size.items() if len(v) > 1]
        for i, (size, files) in enumerate(groups):
            if self.cancel.is_set():
                raise Cancelled()
            self._tick(files[0][0], f" · duplicados {i + 1}/{len(groups)}")
            by_part = defaultdict(list)
            for f in files:
                try:
                    by_part[partial_hash(f[0], size)].append(f)
                except OSError:
                    continue
            for cands in by_part.values():
                if len(cands) < 2:
                    continue
                by_full = defaultdict(list)
                for f in cands:
                    try:
                        by_full[hash_file(f[0], self.cancel)].append(f)
                    except OSError:
                        continue
                for same in by_full.values():
                    if len(same) > 1:
                        self._dup_group(same, size)

    def _dup_group(self, same, size):
        free = sorted([f for f in same if not f[2]], key=lambda f: (f[1], len(f[0])))  # fuera de proyectos
        in_proj = [f for f in same if f[2]]
        if len(free) < 2:
            return  # nunca se propone la única copia "suelta" ni copias dentro de proyectos (in_proj)
        keep = free[0]
        for f in free[1:]:
            self.recs["dups"].items.append(Item(f[0], size, dup_where(f[0], keep[0]), ref=keep[0]))

    # ── armado final
    def run(self) -> Report:
        t0 = time.time()
        rep = Report(self.root)
        top = [] if self.map_on else None
        total, _ = self.walk(self.root, 0, in_dl=is_inside(self.root, self.downloads), out=top)
        rep.tree = top[0] if top else None
        for n in list(self.special):  # temporales/caches que quedaron fuera de la carpeta analizada
            if n not in self._seen_special:
                self._special(n, 1)
        if self.find_dups:
            self._dups()
        for p, size, newest in self.projects:
            if newest < self.old_cut and size:
                self.recs["projects"].items.append(Item(p, size, f"último cambio hace {age_txt(newest, self.now)}"))
        if self.recycle:
            info = recycle_bin_info()
            if info and info[0]:
                self.recs["bin"].items.append(Item(BIN_PATH, info[0], f"{info[1]} elementos"))
        for r in self.recs.values():
            r.items.sort(key=lambda it: -it.size)
        rep.recs = [r for r in self.recs.values() if r.items]
        rep.total, rep.n_files, rep.n_dirs = total, self.n_files, self.n_dirs
        rep.cloud, rep.errors = self.cloud, self.errors
        rep.top_files = [(s, mt, p, co) for s, p, mt, co in sorted(self.heap, reverse=True)]
        d1 = sorted(((p, s) for p, (s, d) in self.dir_sizes.items() if d == 1), key=lambda x: -x[1])
        kids = defaultdict(list)
        for p, (s, d) in self.dir_sizes.items():
            if d == 2:
                kids[os.path.dirname(p)].append((p, s))
        rep.top_dirs = [(p, s, sorted(kids[p], key=lambda x: -x[1])[:6]) for p, s in d1[:20]]
        rep.elapsed = time.time() - t0
        rep.created = time.time()
        return rep


def analyze(root, cancel=None, progress=None, **kw) -> Report:
    return Analyzer(root, cancel, progress, **kw).run()


# ───────────────────────────── motor: BORRADO ─────────────────────────────

def is_protected(p) -> bool:
    """Lo que la app se niega a borrar aunque se lo pidas."""
    a = os.path.abspath(str(p))
    if is_disk_root(a):
        return True  # raíz de un disco
    home = os.path.abspath(str(Path.home()))
    if is_inside(home, a):
        return True  # el home o algo que lo contiene
    for name in ("Desktop", "Documents", "Downloads", "Music", "Pictures", "Videos", "AppData", "OneDrive",
                 "Escritorio", "Documentos", "Descargas", "Música", "Imágenes",
                 "Library", "Movies", "Public", "Applications", ".Trash"):
        if _norm(a) == _norm(os.path.join(home, name)):
            return True
    temps = default_temp_dirs()
    for t in temps:
        if _norm(a) == _norm(t):
            return True  # la carpeta TEMP en sí no, su contenido sí
    in_temp = any(is_inside(a, t) for t in temps)  # en Mac la temporal vive dentro de /private
    win_temp = os.path.join(os.environ.get("WINDIR", ""), "Temp")
    for sd in _sysdirs():
        if sd and is_inside(a, sd) and not in_temp:
            if not (os.environ.get("WINDIR") and is_inside(a, win_temp) and _norm(a) != _norm(win_temp)):
                return True
    return False


def path_size(p) -> int:
    try:
        st = os.lstat(p)
    except OSError:
        return 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_size
    total = 0
    for root, _d, files in os.walk(p):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def delete_paths(paths, cancel=None, emit=None, log_dir=None):
    """Borra para siempre (libera espacio de verdad). Devuelve dict con freed/deleted/errors/log_path."""
    emit = emit or (lambda *a: None)
    log_dir = Path(log_dir) if log_dir else DELETE_LOG_DIR
    res = dict(freed=0, deleted=[], errors=0, log_path="")
    log = None
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        lp = log_dir / f"{datetime.now():%Y-%m-%d_%H%M%S}_borrado.log"
        log = open(lp, "w", encoding="utf-8")
        log.write(f"# {APP_NAME} {VERSION} · borrado · {datetime.now().isoformat(timespec='seconds')}\n")
        res["log_path"] = str(lp)
    except OSError:
        pass

    def record(status, p, size=0, msg=""):
        if log:
            log.write(f"{status}\t{size}\t{p}\t{msg}\n")
            log.flush()

    def on_rm_error(func, path, exc_info=None):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError:
            pass  # en uso o sin permiso: queda, se cuenta abajo

    try:
        for i, p in enumerate(paths, 1):
            if cancel is not None and cancel.is_set():
                emit("log", "warn", "Borrado cancelado. Lo que quedaba no se tocó.")
                break
            emit("current", f"borrando [{i}/{len(paths)}] {p}")
            if is_protected(p):
                res["errors"] += 1
                emit("log", "err", f"Protegido, no lo borro: {p}")
                record("PROTEGIDO", p)
                continue
            if not os.path.lexists(p):
                continue
            before = path_size(p)
            try:
                if os.path.isdir(p) and not os.path.islink(p):
                    if sys.version_info >= (3, 12):
                        shutil.rmtree(p, onexc=on_rm_error)
                    else:
                        shutil.rmtree(p, onerror=on_rm_error)
                else:
                    remove_file(p)
            except OSError as e:
                emit("log", "err", f"No pude borrar {p}: {e}")
            left = path_size(p) if os.path.lexists(p) else 0
            res["freed"] += max(0, before - left)
            if os.path.lexists(p):
                res["errors"] += 1
                record("PARCIAL" if left < before else "ERROR", p, before - left)
                if left < before:
                    emit("log", "warn", f"Borrado parcial (algo estaba en uso): {p}")
                else:
                    emit("log", "err", f"No se pudo borrar (en uso o sin permiso): {p}")
            else:
                res["deleted"].append(p)
                record("BORRADO", p, before)
    finally:
        if log:
            log.close()
    return res


# ───────────────────────────── motor: PRESETS / PROGRAMADO ─────────────────────────────

PRESETS = {
    "limpieza": dict(
        title="Limpieza semanal", action="delete", recs=("bin", "temp", "cache", "deps", "installers"),
        desc="Borra solo lo [SEGURO]: papelera, temporales, caches, dependencias de proyectos quietos "
             "e instaladores viejos de Descargas. Nunca toca lo de REVISAR ni lo del VAULT."),
    "sesiones": dict(
        title="Sesiones viejas", action="vault", recs=("projects", "media"),
        desc="Guarda en el HDD (MOVER, verificado con SHA-256) los proyectos de DAW/video y la media pesada "
             "sin tocar hace más de 6 meses, con ruta completa."),
}
CACHE_QUIET_DAYS = 2   # una cache usada hace menos que esto puede estar abierta (VS Code, next dev…)


def preset_paths(rep, key, now=None):
    """Lo que tocaría un preset: (rutas, bytes, salteados[(ruta, motivo)])."""
    now = now or time.time()
    paths, skipped, total = [], [], 0
    for r in rep.recs:
        if r.key not in PRESETS[key]["recs"]:
            continue
        for it in r.items:
            if r.key == "cache" and it.mtime > now - CACHE_QUIET_DAYS * DAY:
                skipped.append((it.path, f"se usó en los últimos {CACHE_QUIET_DAYS} días, puede estar abierta"))
                continue
            paths.append(it.path)
            total += it.size
    return paths, total, skipped


def run_preset(key, root, dest=None, cancel=None, emit=None, **analyze_kw):
    """Corre un preset entero sin preguntar nada (modo programado). Devuelve un resumen."""
    emit = emit or (lambda *a: None)
    cancel = cancel or threading.Event()
    p = PRESETS[key]
    res = dict(preset=key, title=p["title"], found=0, done=0, freed=0, errors=0, skipped=0, log_path="", note="")
    if p["action"] == "vault" and not (dest and os.path.isdir(dest)):
        res["note"] = f"el destino no está conectado ({dest or 'sin elegir'}), no hice nada"
        return res
    emit("log", "info", f"analizando {root}…")
    analyze_kw["recycle"] = False  # la papelera es la red de seguridad: sin nadie mirando, nunca se vacía
    rep = analyze(root, cancel, find_dups=False, map_tree=False, **analyze_kw)
    paths, total, skipped = preset_paths(rep, key)
    paths = [x for x in paths if x != BIN_PATH]
    res.update(found=total, skipped=len(skipped))
    for path, why in skipped:
        emit("log", "info", f"salteo {path}: {why}")
    if not paths:
        res["note"] = "nada para hacer"
        return res

    if p["action"] == "delete":
        if paths:
            d = delete_paths(paths, cancel, emit)
            res["freed"] += d["freed"]
            res["done"] += len(d["deleted"])
            res["errors"] += d["errors"]
            res["log_path"] = d["log_path"]
        return res

    ok = []
    for path in paths:  # sin nadie mirando: cualquier aviso del preflight = no se toca
        errs, warns = preflight([path], dest, MODE_MOVE)
        if errs or warns:
            res["skipped"] += 1
            emit("log", "warn", f"salteo {path}: {(errs + warns)[0].splitlines()[0]}")
        else:
            ok.append(path)
    if ok:
        st = run_job(ok, dest, MODE_MOVE, cancel, emit, 0, True)
        res.update(freed=st["freed"], done=st["deleted"], log_path=st["log_path"])
        res["errors"] += st["errors"]
        res["skipped"] += st["skipped"]
        if st["aborted"]:
            res["note"] = "no entra en el destino, no hice nada"
        elif st["cancelled"]:
            res["note"] = "cancelado"
    return res


def auto_main(argv) -> int:
    """Entrada sin ventana: limpia_guarda.py --preset limpieza|sesiones [--root X] [--dest Y]."""
    import argparse
    ap = argparse.ArgumentParser(prog="limpia_guarda", description=f"{APP_NAME} en modo programado")
    ap.add_argument("--preset", choices=list(PRESETS), required=True)
    ap.add_argument("--root", help="carpeta a analizar (por defecto la de la app)")
    ap.add_argument("--dest", help="destino en el HDD (por defecto el de la app)")
    a = ap.parse_args(argv)
    cfg = load_config()
    root = a.root or cfg.get("an_root") or str(Path.home())
    dest = a.dest or cfg.get("dest", "")
    notes = []

    def emit(kind, *args):
        if kind == "log" and args[0] in ("warn", "err") and len(notes) < 40:
            notes.append(f"    {args[0]}: {args[1]}")

    try:
        res = run_preset(a.preset, root, dest, None, emit)
    except Exception as e:  # noqa: BLE001
        res = dict(title=PRESETS[a.preset]["title"], done=0, freed=0, errors=1, skipped=0, log_path="",
                   note=f"falló: {e}")
    line = (f"{datetime.now():%Y-%m-%d %H:%M}  {res['title']} · {root} · liberado {human(res['freed'])} · "
            f"{res['done']} hechos · {res['skipped']} salteados · {res['errors']} errores")
    if res.get("note"):
        line += f" · {res['note']}"
    if res.get("log_path"):
        line += f" · log: {res['log_path']}"
    add_freed(res["freed"])
    try:
        AUTO_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(AUTO_LOG, "a", encoding="utf-8") as f:
            f.write("\n".join([line] + notes) + "\n")
    except OSError:
        pass
    print(line)
    return 1 if res["errors"] else 0


# Programador de tareas de Windows (en Mac, launchd: más abajo)
SCHEDULER = "launchd de macOS" if IS_MAC else "el Programador de tareas de Windows"
OS_NAME = "macOS" if IS_MAC else "Windows"
WHEN_OPTIONS = [("cada domingo", "Sunday"), ("cada lunes", "Monday"), ("cada martes", "Tuesday"),
                ("cada miércoles", "Wednesday"), ("cada jueves", "Thursday"), ("cada viernes", "Friday"),
                ("cada sábado", "Saturday"), ("el día 1 de cada mes", "M1")]
HOURS = [f"{h:02d}:00" for h in range(24)]
AUTO_DEFAULTS = {"limpieza": ("cada domingo", "20:00"), "sesiones": ("el día 1 de cada mes", "20:00")}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")
NO_WINDOW = 0x08000000


def task_name(key) -> str:
    return f"\\{TASK_FOLDER}\\{key}"


def task_command(key, root, dest):
    """(programa, argumentos) que va a ejecutar Windows. Con pythonw no aparece ninguna ventana."""
    if getattr(sys, "frozen", False):  # el .exe de PyInstaller
        cmd, args = sys.executable, []
    else:
        py = Path(sys.executable)
        pyw = py.with_name("pythonw.exe")
        cmd, args = str(pyw if pyw.exists() else py), [os.path.abspath(__file__)]
    args += ["--preset", key, "--root", root]
    if dest:
        args += ["--dest", dest]
    return cmd, subprocess.list2cmdline(args)


def task_xml(key, when, hour, root, dest, now=None) -> str:
    from xml.sax.saxutils import escape
    code = dict(WHEN_OPTIONS)[when]
    start = datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-%d") + f"T{hour}:00"
    if code == "M1":
        sched = ("<ScheduleByMonth><DaysOfMonth><Day>1</Day></DaysOfMonth><Months>"
                 + "".join(f"<{m}/>" for m in MONTHS) + "</Months></ScheduleByMonth>")
    else:
        sched = f"<ScheduleByWeek><DaysOfWeek><{code}/></DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>"
    cmd, args = task_command(key, root, dest)
    user = "\\".join(x for x in (os.environ.get("USERDOMAIN"), os.environ.get("USERNAME")) if x)
    workdir = os.path.dirname(cmd if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{escape(f"{APP_NAME} · {PRESETS[key]['title']}")}</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>{start}</StartBoundary><Enabled>true</Enabled>{sched}</CalendarTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT6H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(cmd)}</Command><Arguments>{escape(args)}</Arguments><WorkingDirectory>{escape(workdir)}</WorkingDirectory></Exec></Actions>
</Task>
"""


def _schtasks(*args):
    return subprocess.run(["schtasks", *args], capture_output=True, encoding="oem", errors="replace",
                          creationflags=NO_WINDOW if sys.platform.startswith("win") else 0)


# launchd (Mac): un .plist por tarea en ~/Library/LaunchAgents
LAUNCHD_PREFIX = "com.teskadev.limpiaguarda"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
WEEKDAYS = {"Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3, "Thursday": 4, "Friday": 5, "Saturday": 6}


def launchd_label(key) -> str:
    return f"{LAUNCHD_PREFIX}.{key}"


def launchd_plist_path(key) -> Path:
    return LAUNCH_AGENTS / f"{launchd_label(key)}.plist"


def launchd_args(key, root, dest) -> list:
    if getattr(sys, "frozen", False):  # el ejecutable adentro de LimpiaGuarda.app
        args = [sys.executable]
    else:
        args = [sys.executable, os.path.abspath(__file__)]
    args += ["--preset", key, "--root", root]
    if dest:
        args += ["--dest", dest]
    return args


def launchd_plist(key, when, hour, root, dest) -> dict:
    code = dict(WHEN_OPTIONS)[when]
    h = int(hour.split(":")[0])
    cal = {"Day": 1, "Hour": h, "Minute": 0} if code == "M1" else {"Weekday": WEEKDAYS[code], "Hour": h, "Minute": 0}
    return {"Label": launchd_label(key), "ProgramArguments": launchd_args(key, root, dest),
            "StartCalendarInterval": cal, "ProcessType": "Background", "LowPriorityIO": True,
            "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null"}


def mac_translocated() -> bool:
    """macOS corre la app desde una carpeta temporal si no la moviste a Aplicaciones."""
    return getattr(sys, "frozen", False) and "/AppTranslocation/" in sys.executable


def _launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, errors="replace")


def _gui_domain() -> str:
    return f"gui/{os.getuid()}"


def _mac_schedule(key, when, hour, root, dest):
    import plistlib
    if mac_translocated():
        return False, ("Primero mové LimpiaGuarda a la carpeta Aplicaciones y abrila desde ahí. "
                       "Si no, macOS la corre desde una carpeta temporal que después desaparece.")
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    path = launchd_plist_path(key)
    _launchctl("bootout", f"{_gui_domain()}/{launchd_label(key)}")  # si ya estaba, se reemplaza
    with open(path, "wb") as f:
        plistlib.dump(launchd_plist(key, when, hour, root, dest), f)
    r = _launchctl("bootstrap", _gui_domain(), str(path))
    if r.returncode != 0:
        r = _launchctl("load", "-w", str(path))  # forma vieja, por si acaso
    return r.returncode == 0, (r.stdout or r.stderr).strip()


def _mac_unschedule(key):
    path = launchd_plist_path(key)
    _launchctl("bootout", f"{_gui_domain()}/{launchd_label(key)}")
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        return False, str(e)
    return True, ""


def _mac_run_now(key):
    r = _launchctl("kickstart", f"{_gui_domain()}/{launchd_label(key)}")
    return r.returncode == 0, (r.stdout or r.stderr).strip()


def schedule_task(key, when, hour, root, dest):
    """Crea (o reemplaza) la tarea. Devuelve (ok, mensaje)."""
    if IS_MAC:
        return _mac_schedule(key, when, hour, root, dest)
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-16") as f:
            f.write(task_xml(key, when, hour, root, dest))
        r = _schtasks("/Create", "/TN", task_name(key), "/XML", tmp, "/F")
        return r.returncode == 0, (r.stdout or r.stderr).strip()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def unschedule_task(key):
    if IS_MAC:
        return _mac_unschedule(key)
    r = _schtasks("/Delete", "/TN", task_name(key), "/F")
    return r.returncode == 0, (r.stdout or r.stderr).strip()


def task_exists(key) -> bool:
    if IS_MAC:
        return launchd_plist_path(key).is_file()
    try:
        return _schtasks("/Query", "/TN", task_name(key)).returncode == 0
    except OSError:
        return False


def run_task_now(key):
    if IS_MAC:
        return _mac_run_now(key)
    r = _schtasks("/Run", "/TN", task_name(key))
    return r.returncode == 0, (r.stdout or r.stderr).strip()


def _stats_path() -> Path:
    return DELETE_LOG_DIR / "total.json"   # se calcula al usarlo (así las pruebas lo pueden redirigir)


def total_freed() -> int:
    try:
        return int(json.loads(_stats_path().read_text(encoding="utf-8")).get("freed", 0))
    except Exception:  # noqa: BLE001
        return 0


def add_freed(n) -> int:
    """Suma al contador histórico de espacio liberado. Devuelve el total nuevo."""
    tot = total_freed() + max(0, int(n or 0))
    try:
        _stats_path().parent.mkdir(parents=True, exist_ok=True)
        _stats_path().write_text(json.dumps({"freed": tot}), encoding="utf-8")
    except OSError:
        pass
    return tot


def read_auto_log(n=14) -> list:
    try:
        lines = AUTO_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return lines[-n:]


# ───────────────────────────── mapa visual (squarified treemap) ─────────────────────────────

def squarify(items, x, y, w, h):
    """items: [(tamaño, obj)] de mayor a menor. Devuelve [(x, y, w, h, obj)] con rectángulos lo más cuadrados posible."""
    items = [(s, o) for s, o in items if s > 0]
    total = sum(s for s, _ in items)
    if total <= 0 or w <= 0 or h <= 0:
        return []
    scale = w * h / total
    rest = [(s * scale, o) for s, o in items]
    out = []

    def worst(row, side):
        s = sum(a for a, _ in row)
        mx, mn = row[0][0], row[-1][0]
        return max(side * side * mx / (s * s), (s * s) / (side * side * mn))

    while rest:
        side = min(w, h)
        if side <= 0:
            break
        row, i = [rest[0]], 1
        while i < len(rest) and worst(row + [rest[i]], side) <= worst(row, side):
            row.append(rest[i])
            i += 1
        rest = rest[i:]
        s = sum(a for a, _ in row)
        if w >= h:  # columna a la izquierda
            cw, yy = s / h, y
            for a, o in row:
                out.append((x, yy, cw, a / cw, o))
                yy += a / cw
            x, w = x + cw, w - cw
        else:       # fila arriba
            rh, xx = s / w, x
            for a, o in row:
                out.append((xx, y, a / rh, rh, o))
                xx += a / rh
            y, h = y + rh, h - rh
    return out


# ───────────────────────────── config / SO ─────────────────────────────

def migrate_legacy():
    """Primera vez como Limpia&Guarda: trae la config y los logs de Teska Vault. No borra nada.
    Solo se llama desde main(), nunca desde las pruebas."""
    msgs = []
    try:
        if not CONFIG_PATH.exists() and LEGACY_CONFIG_PATH.is_file():
            shutil.copy2(LEGACY_CONFIG_PATH, CONFIG_PATH)  # la vieja queda como respaldo
            msgs.append(f"config copiada de {LEGACY_CONFIG_PATH.name}")
    except OSError as e:
        msgs.append(f"no pude copiar la config vieja: {e}")
    try:
        if not DELETE_LOG_DIR.exists() and LEGACY_DELETE_LOG_DIR.is_dir():
            os.rename(LEGACY_DELETE_LOG_DIR, DELETE_LOG_DIR)  # renombrar: los logs no se pierden
            msgs.append(f"logs movidos de {LEGACY_DELETE_LOG_DIR.name} a {DELETE_LOG_DIR.name}")
    except OSError:
        try:
            shutil.copytree(LEGACY_DELETE_LOG_DIR, DELETE_LOG_DIR)
            msgs.append(f"logs copiados de {LEGACY_DELETE_LOG_DIR.name} a {DELETE_LOG_DIR.name}")
        except OSError as e:
            msgs.append(f"no pude traer los logs viejos: {e}")
    return msgs


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def save_config(cfg):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def open_folder(path):
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def reveal(path):
    """Abre el explorador con el archivo seleccionado."""
    if sys.platform.startswith("win") and os.path.exists(path):
        subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
    elif IS_MAC and os.path.exists(path):
        subprocess.Popen(["open", "-R", path])  # Finder con el archivo seleccionado
    else:
        open_folder(path if os.path.isdir(path) else os.path.dirname(path))


# ───────────────────────────── GUI ─────────────────────────────

BG = "#0a0a0a"
PANEL = "#101510"
FG = "#33ff66"
DIM = "#1e7a3c"
TEXT = "#c8ffd8"
MUTED = "#5f7f6a"
WARN = "#ffcc33"
ERR = "#ff5c7a"
CYAN = "#5ce1ff"
MONO = "Consolas" if IS_WIN else ("Menlo" if IS_MAC else "DejaVu Sans Mono")

if tk is not None:
    class FlatButton(tk.Label):
        """Botón para Mac: los tk.Button de macOS ignoran los colores, así que se dibuja con un Label."""

        def __init__(self, parent, command=None, **kw):
            self._active = (kw.pop("activebackground", TEXT), kw.pop("activeforeground", BG))
            self._saved = None  # colores de antes del hover
            super().__init__(parent, **kw)
            self._command = command
            self.bind("<Enter>", lambda e: self._set_hover(True))
            self.bind("<Leave>", lambda e: self._set_hover(False))
            self.bind("<ButtonRelease-1>", self._click)

        def _set_hover(self, on):
            if on and self._saved is None and str(self.cget("state")) != "disabled":
                self._saved = (self.cget("bg"), self.cget("fg"))
                super().configure(bg=self._active[0], fg=self._active[1])
            elif not on and self._saved is not None:
                bg, fg = self._saved
                self._saved = None
                super().configure(bg=bg, fg=fg)

        def _click(self, e):
            inside = 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height()
            if inside and str(self.cget("state")) != "disabled" and self._command:
                self._command()

        def invoke(self):
            if str(self.cget("state")) != "disabled" and self._command:
                return self._command()

        def configure(self, cnf=None, **kw):
            if "command" in kw:
                self._command = kw.pop("command")
            if "activebackground" in kw or "activeforeground" in kw:
                self._active = (kw.pop("activebackground", self._active[0]),
                                kw.pop("activeforeground", self._active[1]))
            if self._saved is not None and (kw.get("state") == "disabled" or {"bg", "fg"} & set(kw)):
                self._set_hover(False)  # un cambio explícito de colores o estado le gana al hover
            return super().configure(cnf, **kw)

        config = configure
MAX_ROWS = 300

# colores del mapa (relleno de cada tipo de bloque)
MAP_COLORS = [("media / RAW", "#1d5f70"), ("proyecto DAW/video", "#6a3d7a"),
              ("instalador / comprimido", "#7a5c12"), ("otro archivo", "#1f6b3a"),
              ("carpeta (detalle chico)", "#173d25"), ("archivos chicos juntos", "#1a201a")]
MAP_DIR_BG = ("#0d120e", "#111912")
MAP_HEAD = 15
MAP_MAX_ITEMS = 5000


def map_fill(node) -> str:
    if node.kind == "rest":
        return MAP_COLORS[5][1]
    if node.kind == "dir":
        return MAP_COLORS[4][1]
    ext = os.path.splitext(node.name)[1].lower()
    if ext in MEDIA_EXT:
        return MAP_COLORS[0][1]
    if ext in DAW_EXT:
        return MAP_COLORS[1][1]
    if ext in INSTALLER_EXT or ext in ARCHIVE_EXT:
        return MAP_COLORS[2][1]
    return MAP_COLORS[3][1]


class TreeMapView:
    """Mapa de bloques tipo WizTree: cada rectángulo ocupa lo que ocupa en disco.
    Clic = seleccionar · doble clic = entrar a la carpeta · clic derecho / SUBIR = volver."""

    def __init__(self, app, parent):
        self.app = app
        top = tk.Frame(parent, bg=BG)
        top.pack(fill="x", pady=(4, 4))
        self.b_up = app._btn(top, "▲ SUBIR", self.up)
        self.b_up.pack(side="left")
        self.crumb = tk.StringVar(value="")
        tk.Label(top, textvariable=self.crumb, bg=BG, fg=TEXT, font=(MONO, 9), anchor="w").pack(
            side="left", fill="x", expand=True, padx=8)
        self.canvas = tk.Canvas(parent, bg=BG, highlightthickness=1, highlightbackground=DIM, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        leg = tk.Frame(parent, bg=BG)
        leg.pack(fill="x", pady=(4, 0))
        for name, color in MAP_COLORS:
            tk.Label(leg, text="  ", bg=color).pack(side="left", padx=(0, 4))
            tk.Label(leg, text=name, bg=BG, fg=MUTED, font=(MONO, 8)).pack(side="left", padx=(0, 12))
        self.stack: list = []
        self.items: dict = {}
        self.sel = None
        self._job = None
        self._hl = self._sel_rect = None
        c = self.canvas
        c.bind("<Configure>", lambda e: self._schedule())
        c.bind("<Motion>", self._on_motion)
        c.bind("<Leave>", lambda e: c.coords(self._hl, -9, -9, -9, -9) if self._hl else None)
        c.bind("<Button-1>", self._on_click)
        c.bind("<Double-1>", self._on_double)
        c.bind("<Button-3>", lambda e: self.up())
        if IS_MAC:  # clic derecho en Mac: Button-2 o ctrl+clic
            c.bind("<Button-2>", lambda e: self.up())
            c.bind("<Control-Button-1>", lambda e: self.up())

    # ── datos
    def set_tree(self, node, keep_zoom=False):
        if keep_zoom and node is not None and self.stack and self.stack[0] is node:
            gone = [n for n in self.stack if n is not node and n not in self._alive(node)]
            if gone:
                self.stack = [node]
        else:
            self.stack = [node] if node is not None else []
        self.sel = None
        self._schedule()

    @staticmethod
    def _alive(root):
        out, todo = set(), [root]
        while todo:
            n = todo.pop()
            out.add(n)
            todo.extend(k for k in n.kids if k.kind == "dir")
        return out

    def up(self):
        if len(self.stack) > 1:
            self.stack.pop()
            self.sel = None
            self.redraw()

    # ── dibujo
    def _schedule(self):
        if self._job:
            self.canvas.after_cancel(self._job)
        self._job = self.canvas.after(60, self.redraw)

    def redraw(self):
        self._job = None
        c = self.canvas
        c.delete("all")
        self.items.clear()
        w, h = c.winfo_width(), c.winfo_height()
        self.b_up.configure(state="normal" if len(self.stack) > 1 else "disabled")
        if not self.stack:
            self.crumb.set("")
            c.create_text(w / 2, h / 2, text="analizá una carpeta para ver el mapa", fill=MUTED, font=(MONO, 10))
            self._hl = self._sel_rect = None
            return
        cur = self.stack[-1]
        tot = self.stack[0].size or 1
        self.crumb.set(f"{cur.path}  ·  {human(cur.size)}  ·  {cur.size * 100 / tot:.1f}% de lo analizado"
                       + ("   (doble clic = entrar · clic derecho = subir)" if len(self.stack) == 1 else ""))
        self._count = 0
        self._draw(cur, 1, 1, w - 2, h - 2, 0, None)
        self._hl = c.create_rectangle(-9, -9, -9, -9, outline=TEXT, width=1)
        self._sel_rect = c.create_rectangle(-9, -9, -9, -9, outline=WARN, width=2)
        if self.sel is not None:
            for node, box, _parent in self.items.values():
                if node is self.sel:
                    self._place(self._sel_rect, box)
                    break

    def _draw(self, node, x, y, w, h, depth, parent):
        if w < 2 or h < 2 or self._count > MAP_MAX_ITEMS:
            return
        self._count += 1
        c = self.canvas
        entry = (node, (x, y, w, h), parent)
        container = node.kind == "dir" and bool(node.kids)
        fill = MAP_DIR_BG[depth % 2] if container else map_fill(node)
        rid = c.create_rectangle(x, y, x + w, y + h, fill=fill, outline=BG if not container else DIM)
        self.items[rid] = entry
        if container:
            if w > 34 and h > MAP_HEAD + 10:
                self._label(x + 3, y + 1, w - 6, f"{node.name}  {human(node.size)}", FG, entry)
                inner = (x + 2, y + MAP_HEAD, w - 4, h - MAP_HEAD - 2)
            elif w > 8 and h > 8:
                inner = (x + 1, y + 1, w - 2, h - 2)
            else:
                return
            for cx, cy, cw, ch, kid in squarify([(k.size, k) for k in node.kids], *inner):
                self._draw(kid, cx, cy, cw, ch, depth + 1, node)
        elif w > 44 and h > 15:
            name = node.name if node.kind != "rest" else "otros (chicos)"
            self._label(x + 3, y + 2, w - 6, name, TEXT, entry)
            if h > 30:
                self._label(x + 3, y + 15, w - 6, human(node.size), MUTED, entry)

    def _label(self, x, y, w, text, color, entry):
        maxc = int(w / 6.5)
        if maxc < 3:
            return
        if len(text) > maxc:
            text = text[:maxc - 1] + "…"
        tid = self.canvas.create_text(x, y, text=text, anchor="nw", fill=color, font=(MONO, 8))
        self.items[tid] = entry

    def _place(self, rect, box):
        x, y, w, h = box
        self.canvas.coords(rect, x, y, x + w, y + h)
        self.canvas.tag_raise(rect)

    def _hit(self, ev):
        ids = [i for i in self.canvas.find_overlapping(ev.x, ev.y, ev.x, ev.y) if i in self.items]
        return self.items[max(ids)] if ids else (None, None, None)

    # ── eventos
    def _on_motion(self, ev):
        node, box, _parent = self._hit(ev)
        if node is None or self._hl is None:
            return
        self._place(self._hl, box)
        tot = self.stack[0].size or 1
        if node.kind == "rest":
            txt = f"archivos chicos y carpetas menores, juntos · {human(node.size)}"
        else:
            txt = f"{node.path}  ·  {human(node.size)}  ·  {node.size * 100 / tot:.1f}%"
        self.app.map_hint.set(txt)

    def _on_click(self, ev):
        node, box, _parent = self._hit(ev)
        if node is None or node.kind == "rest" or not node.path:
            self.sel = None
            if self._sel_rect:
                self.canvas.coords(self._sel_rect, -9, -9, -9, -9)
            return
        self.sel = node
        self._place(self._sel_rect, box)
        info = self.app.path_info.setdefault(node.path, (node.size, "manual"))
        why = {"seguro": "recomendado: SEGURO", "revisar": "recomendado: REVISAR",
               "vault": "recomendado: → HDD", "sistema": "archivo de programa/sistema"}.get(info[1], "")
        self.app.map_hint.set(f"selección: {human(node.size)}  ·  {node.path}" + (f"  ·  {why}" if why else ""))

    def _on_double(self, ev):
        node, _box, parent = self._hit(ev)
        if node is None:
            return
        if not (node.kind == "dir" and node.kids):  # archivo / bloque suelto: entro a su carpeta
            node = parent
        if node is not None and node is not self.stack[-1]:
            self.stack.append(node)
            self.sel = None
            self.redraw()


CARD_DEFAULT = {"seguro": True, "revisar": False, "vault": True}
SAFETY_COLOR = {"seguro": FG, "revisar": WARN, "vault": CYAN}
SAFETY_TAG = {"seguro": "SEGURO", "revisar": "REVISAR", "vault": "→ HDD"}
DISK_USED = "#1b4d2c"
RULE_TXT = ("regla #1: nada se borra sin tu OK · lo que va al HDD se verifica con SHA-256 antes de borrar "
            "el original · todo queda anotado en un log")


class App:
    def __init__(self, root, dnd_files=None):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.worker = None
        self.cfg = load_config()
        self.sources: list[str] = []
        self._saved_sources = None   # lista manual que se guarda mientras GUARDAR usa la suya
        self.dest = tk.StringVar(value=self.cfg.get("dest", ""))
        self.mode = tk.StringVar(value=self.cfg.get("mode", MODE_MOVE))
        self.age = tk.StringVar(value=self.cfg.get("age", AGE_OPTIONS[0][0]))
        self.full_path = tk.BooleanVar(value=self.cfg.get("full_path", False))
        self.an_root = tk.StringVar(value=self.cfg.get("an_root", str(Path.home())))
        self.status = tk.StringVar(value="listo. dale a ▶ ANALIZAR: miro todo sin tocar nada.")
        self.stats_txt = tk.StringVar(value="")
        self.last_msg = tk.StringVar(value="")
        self.an_summary = tk.StringVar(value="todavía no analizaste nada. arrancá en INICIO.")
        self.an_hint = tk.StringVar(value="")
        self.map_hint = tk.StringVar(value="clic = seleccionar · doble clic = entrar · clic derecho = subir")
        self.total_var = tk.StringVar(value="")
        self.dest_txt = tk.StringVar(value="")
        self.report: Report | None = None
        self.iid_paths: dict = {}
        self.iid_hint: dict = {}
        self.path_info: dict = {}   # ruta -> (tamaño, safety)
        self.auto_ui: dict = {}
        self.cards_on: dict = dict(self.cfg.get("cards", {}))
        self.cards: dict = {}
        self.disk = None            # (drive, total, used, free) del disco analizado
        self._anims: dict = {}
        self._scan_phase = None     # animación de barrido mientras analiza
        self._flash = None          # (texto, hasta) sobre la barra del disco
        self._is_busy = False
        self.hero = {"clean": 0.0, "keep": 0.0, "free": 0.0}

        root.title(f"{APP_NAME} v{VERSION} · {APP_BY}")
        try:  # icono: junto al .py, o dentro del EXE de PyInstaller
            base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
            if IS_WIN:
                root.iconbitmap(default=str(base / "limpiateska.ico"))
            elif not getattr(sys, "frozen", False):  # la .app de Mac ya trae su icono en el Dock
                self._icon = tk.PhotoImage(file=str(base / "limpiateska.png"))
                root.iconphoto(True, self._icon)
        except Exception:  # noqa: BLE001
            pass
        root.configure(bg=BG)
        root.geometry("1000x860")
        root.minsize(800, 700)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._style()
        self._build(dnd_files)
        self._refresh_disk(animate=False)
        self._update_totals(animate=False)
        self._render_cards()
        self._blink()
        self.root.after(80, self._poll)

    # ── estilo
    def _style(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure("Vault.Horizontal.TProgressbar", troughcolor=PANEL, background=FG,
                    bordercolor=DIM, lightcolor=FG, darkcolor=FG, thickness=10)
        s.configure("Vault.TNotebook", background=BG, borderwidth=0, tabmargins=(0, 4, 0, 0))
        s.configure("Vault.TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(14, 6),
                    font=(MONO, 10, "bold"), borderwidth=0, bordercolor=DIM, lightcolor=PANEL)
        s.map("Vault.TNotebook.Tab", background=[("selected", DIM)], foreground=[("selected", BG)])
        s.configure("Sub.TNotebook", background=BG, borderwidth=0)
        s.configure("Sub.TNotebook.Tab", background=BG, foreground=MUTED, padding=(12, 4),
                    font=(MONO, 9, "bold"), borderwidth=0, lightcolor=BG)
        s.map("Sub.TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", FG)])
        s.configure("Vault.Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                    rowheight=22, font=(MONO, 9), borderwidth=0)
        s.configure("Vault.Treeview.Heading", background=BG, foreground=FG, font=(MONO, 9, "bold"),
                    relief="flat", borderwidth=0)
        s.map("Vault.Treeview.Heading", background=[("active", PANEL)])
        s.map("Vault.Treeview", background=[("selected", DIM)], foreground=[("selected", "#ffffff")])
        s.configure("Vault.Vertical.TScrollbar", background=PANEL, troughcolor=BG, bordercolor=BG,
                    arrowcolor=FG, lightcolor=PANEL, darkcolor=PANEL)
        s.configure("Vault.TCombobox", fieldbackground=PANEL, background=PANEL, foreground=TEXT,
                    arrowcolor=FG, bordercolor=DIM, lightcolor=PANEL, darkcolor=PANEL)
        s.map("Vault.TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", TEXT)],
              selectbackground=[("readonly", PANEL)], selectforeground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", DIM)
        self.root.option_add("*TCombobox*Listbox.font", (MONO, 10))

    # ── widgets helpers
    def _btn(self, parent, text, cmd, accent=False, danger=False, color=None, big=False):
        if color or accent:
            bg, fg = (color or FG), BG
        else:
            bg, fg = PANEL, (ERR if danger else FG)
        b = (FlatButton if IS_MAC else tk.Button)(parent, text=text, command=cmd, font=(MONO, 12 if big else 10, "bold"),
                      bg=bg, fg=fg, activebackground=TEXT, activeforeground=BG, relief="flat",
                      bd=0, padx=16 if big else 12, pady=9 if big else 5, cursor="hand2", disabledforeground=MUTED,
                      highlightthickness=1, highlightbackground=ERR if danger else (color or DIM))
        b._colors = (bg, fg)  # para restaurar al habilitar
        return b

    def _label(self, parent, text=None, color=MUTED, size=9, bold=False, var=None):
        return tk.Label(parent, text=text, textvariable=var, bg=BG, fg=color,
                        font=(MONO, size, "bold" if bold else "normal"), anchor="w", justify="left")

    def _build(self, dnd_files):
        pad = dict(padx=16)
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", pady=(12, 0), **pad)
        tk.Label(head, text=f"▓▒░ {APP_NAME}", bg=BG, fg=FG, font=(MONO, 20, "bold")).pack(side="left")
        self.cursor = tk.Label(head, text="▌", bg=BG, fg=FG, font=(MONO, 20, "bold"))
        self.cursor.pack(side="left")
        right = tk.Frame(head, bg=BG)
        right.pack(side="right")
        tk.Label(right, text=f"v{VERSION} · {APP_BY}", bg=BG, fg=MUTED, font=(MONO, 9), anchor="e").pack(fill="x")
        tk.Label(right, textvariable=self.total_var, bg=BG, fg=FG, font=(MONO, 9, "bold"), anchor="e").pack(fill="x")
        self._label(self.root, "analizá → limpiá lo que sobra → guardá en el HDD lo que no usás.").pack(fill="x", **pad)

        self.nb = ttk.Notebook(self.root, style="Vault.TNotebook")
        self.nb.pack(fill="both", expand=True, pady=(8, 0), **pad)
        self.tab_home, self.tab_detail, self.tab_map, self.tab_v, self.tab_auto, self.tab_log = (
            tk.Frame(self.nb, bg=BG) for _ in range(6))
        for tab, txt in ((self.tab_home, "INICIO"), (self.tab_detail, "DETALLE"), (self.tab_map, "MAPA"),
                         (self.tab_v, "GUARDA MANUAL"), (self.tab_auto, "AUTO"), (self.tab_log, "LOG")):
            self.nb.add(tab, text=txt)
        self._build_home(self.tab_home)
        self._build_detail(self.tab_detail)
        self._build_map(self.tab_map)
        self._build_vault(self.tab_v, dnd_files)
        self._build_auto(self.tab_auto)
        self._build_log(self.tab_log)
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._refresh_auto()
                     if self.nb.select() == str(self.tab_auto) else None)

        self.bar = ttk.Progressbar(self.root, style="Vault.Horizontal.TProgressbar", maximum=1000)
        self.bar.pack(fill="x", pady=(8, 2), **pad)
        tk.Label(self.root, textvariable=self.status, bg=BG, fg=TEXT, font=(MONO, 9), anchor="w").pack(fill="x", **pad)
        tk.Label(self.root, textvariable=self.stats_txt, bg=BG, fg=FG, font=(MONO, 9, "bold"), anchor="w").pack(fill="x", **pad)
        self.last_lbl = tk.Label(self.root, textvariable=self.last_msg, bg=BG, fg=MUTED, font=(MONO, 9), anchor="w",
                                 cursor="hand2")
        self.last_lbl.pack(fill="x", pady=(0, 10), **pad)
        self.last_lbl.bind("<Button-1>", lambda e: self.nb.select(self.tab_log))

        self._write("ok", f"{APP_NAME} online. {RULE_TXT}.")
        if not dnd_files:
            self._write("info", "tip: instalá 'tkinterdnd2' (pip) para arrastrar y soltar carpetas.")
        last = read_auto_log(40)
        last = [ln for ln in last if ln and not ln.startswith(" ")]
        if last:
            self._write("info", f"última corrida automática: {last[-1]}")
        self._set_total(total_freed())
        self._update_dest_txt()

        self.run_buttons = [self.b_scan, self.b_run, self.b_an, self.b_send, self.b_del, self.b_msend, self.b_mdel,
                            self.b_clean, self.b_keep]
        self.cancel_buttons = [self.b_cancel, self.b_an_cancel]

    # ── pestaña INICIO
    def _build_home(self, p):
        pad = dict(padx=4)
        row = tk.Frame(p, bg=BG)
        row.pack(fill="x", pady=(10, 0), **pad)
        self._label(row, "CARPETA", FG, bold=True).pack(side="left", padx=(0, 8))
        tk.Label(row, textvariable=self.an_root, bg=PANEL, fg=TEXT, font=(MONO, 10), anchor="w",
                 padx=8, pady=5).pack(side="left", fill="x", expand=True)
        self._btn(row, "ELEGIR…", self._pick_an_root).pack(side="left", padx=(8, 0))
        self.b_an = self._btn(row, "▶ ANALIZAR", self._analyze, accent=True)
        self.b_an.pack(side="left", padx=(8, 0))
        self.b_an_cancel = self._btn(row, "■", self._cancel)
        self.b_an_cancel.pack(side="left", padx=(8, 0))
        self.b_an_cancel.configure(state="disabled")

        # tres números grandes
        hero = tk.Frame(p, bg=BG)
        hero.pack(fill="x", pady=(10, 0), **pad)
        self.hero_lbl, self.hero_cap = {}, {}
        for i, (key, color, cap) in enumerate((("clean", FG, "para limpiar"), ("keep", CYAN, "para guardar en el HDD"),
                                               ("free", TEXT, "libres"))):
            box = tk.Frame(hero, bg=PANEL, highlightthickness=1, highlightbackground=DIM)
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            hero.columnconfigure(i, weight=1, uniform="hero")
            self.hero_lbl[key] = tk.Label(box, text="—", bg=PANEL, fg=color, font=(MONO, 22, "bold"), anchor="w")
            self.hero_lbl[key].pack(fill="x", padx=12, pady=(6, 0))
            self.hero_cap[key] = tk.Label(box, text=cap, bg=PANEL, fg=MUTED, font=(MONO, 9), anchor="w")
            self.hero_cap[key].pack(fill="x", padx=12, pady=(0, 8))

        # barra del disco
        self.diskc = tk.Canvas(p, height=26, bg=BG, highlightthickness=0)
        self.diskc.pack(fill="x", pady=(10, 2), **pad)
        self.diskc.bind("<Configure>", lambda e: self._draw_disk())
        leg = tk.Frame(p, bg=BG)
        leg.pack(fill="x", **pad)
        self.leg = {}
        for key, color in (("used", DISK_USED), ("clean", FG), ("keep", CYAN), ("free", PANEL)):
            tk.Label(leg, text="  ", bg=color, highlightthickness=1, highlightbackground=DIM).pack(side="left", padx=(0, 4))
            self.leg[key] = tk.Label(leg, text="", bg=BG, fg=MUTED, font=(MONO, 8))
            self.leg[key].pack(side="left", padx=(0, 14))

        # tarjetas (con scroll si no entran)
        wrap = tk.Frame(p, bg=BG)
        wrap.pack(fill="both", expand=True, pady=(10, 0), **pad)
        self.cards_canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.cards_canvas.yview, style="Vault.Vertical.TScrollbar")
        def autohide(first, last):  # la barra aparece solo si las tarjetas no entran
            if float(first) <= 0 and float(last) >= 1:
                sb.pack_forget()
            else:
                sb.pack(side="left", fill="y")
            sb.set(first, last)
        self.cards_canvas.configure(yscrollcommand=autohide)
        self.cards_canvas.pack(side="left", fill="both", expand=True)
        self.cards_box = tk.Frame(self.cards_canvas, bg=BG)
        win = self.cards_canvas.create_window((0, 0), window=self.cards_box, anchor="nw")
        self.cards_box.bind("<Configure>", lambda e: self.cards_canvas.configure(
            scrollregion=self.cards_canvas.bbox("all")))
        self.cards_canvas.bind("<Configure>", lambda e: self.cards_canvas.itemconfigure(win, width=e.width))
        def wheel(e):  # solo si el mouse está sobre las tarjetas
            w = self.root.winfo_containing(e.x_root, e.y_root)
            if w is not None and str(w).startswith(str(self.cards_canvas)):
                step = -e.delta if IS_MAC else int(-e.delta / 120)  # en Mac delta viene en pasos chicos
                self.cards_canvas.yview_scroll(step, "units")
        self.root.bind_all("<MouseWheel>", wheel, add="+")

        # acciones
        arow = tk.Frame(p, bg=BG)
        arow.pack(fill="x", pady=(10, 0), **pad)
        self.b_clean = self._btn(arow, "✖ LIMPIAR", self._clean, color=FG, big=True)
        self.b_clean.pack(side="left")
        self.b_keep = self._btn(arow, "→ GUARDAR EN EL HDD", self._keep, color=CYAN, big=True)
        self.b_keep.pack(side="left", padx=(10, 0))
        drow = tk.Frame(arow, bg=BG)
        drow.pack(side="right")
        self._label(drow, var=self.dest_txt, color=TEXT).pack(side="left")
        self._btn(drow, "CAMBIAR", self._pick_dest).pack(side="left", padx=(8, 0))
        rule = self._label(p, RULE_TXT, size=8)
        rule.configure(wraplength=940)
        rule.pack(fill="x", pady=(6, 4), **pad)

    # ── pestaña DETALLE
    def _build_detail(self, p):
        pad = dict(padx=4)
        self._label(p, color=TEXT, var=self.an_summary).pack(fill="x", pady=(10, 4), **pad)
        self.anb = ttk.Notebook(p, style="Sub.TNotebook")
        self.anb.pack(fill="both", expand=True, **pad)
        f1, f2, f3 = (tk.Frame(self.anb, bg=BG) for _ in range(3))
        self.anb.add(f1, text="RECOMENDADO")
        self.anb.add(f2, text="ARCHIVOS PESADOS")
        self.anb.add(f3, text="CARPETAS")
        self.t_rec = self._tree(f1, [("size", "tamaño", 100, "e"), ("count", "ítems", 70, "e")],
                                "tree headings", "categoría / ruta")
        self.t_files = self._tree(f2, [("size", "tamaño", 100, "e"), ("age", "sin tocar", 100, "e"),
                                       ("path", "ruta", 600, "w")], "headings")
        self.t_dirs = self._tree(f3, [("size", "tamaño", 110, "e"), ("pct", "%", 60, "e")],
                                 "tree headings", "carpeta")
        self.trees = [self.t_rec, self.t_files, self.t_dirs]
        tk.Label(p, textvariable=self.an_hint, bg=BG, fg=MUTED, font=(MONO, 9), anchor="w", justify="left",
                 wraplength=900).pack(fill="x", pady=(6, 0), **pad)
        arow = tk.Frame(p, bg=BG)
        arow.pack(fill="x", pady=(6, 4), **pad)
        self.b_send = self._btn(arow, "→ GUARDA MANUAL", lambda: self._send_paths(self._selected_paths()))
        self.b_send.pack(side="left")
        self.b_del = self._btn(arow, "✖ BORRAR SELECCIÓN", lambda: self._delete_flow(self._selected_paths()),
                               danger=True)
        self.b_del.pack(side="left", padx=8)
        self._btn(arow, "ABRIR UBICACIÓN", lambda: self._open_paths(self._selected_paths())).pack(side="left")
        self._label(arow, "Ctrl/Shift = varias").pack(side="right")
        self.anb.bind("<<NotebookTabChanged>>", lambda e: self.an_hint.set(
            "seleccioná algo para ver por qué está recomendado." if self.report else ""))

    # ── pestaña MAPA
    def _build_map(self, p):
        pad = dict(padx=4)
        inner = tk.Frame(p, bg=BG)
        inner.pack(fill="both", expand=True, pady=(6, 0), **pad)
        self.map = TreeMapView(self, inner)
        tk.Label(p, textvariable=self.map_hint, bg=BG, fg=MUTED, font=(MONO, 9), anchor="w", justify="left",
                 wraplength=900).pack(fill="x", pady=(6, 0), **pad)
        arow = tk.Frame(p, bg=BG)
        arow.pack(fill="x", pady=(6, 4), **pad)
        self.b_msend = self._btn(arow, "→ GUARDA MANUAL", lambda: self._send_paths(self._map_paths()))
        self.b_msend.pack(side="left")
        self.b_mdel = self._btn(arow, "✖ BORRAR", lambda: self._delete_flow(self._map_paths()), danger=True)
        self.b_mdel.pack(side="left", padx=8)
        self._btn(arow, "ABRIR UBICACIÓN", lambda: self._open_paths(self._map_paths())).pack(side="left")

    # ── pestaña LOG
    def _build_log(self, p):
        fr = tk.Frame(p, bg=BG)
        fr.pack(fill="both", expand=True, pady=(8, 6), padx=4)
        self.log = tk.Text(fr, bg="#050805", fg=FG, font=(MONO, 9), relief="flat", wrap="word",
                           highlightthickness=1, highlightbackground=DIM, insertbackground=FG,
                           padx=8, pady=6, state="disabled")
        sb = ttk.Scrollbar(fr, command=self.log.yview, style="Vault.Vertical.TScrollbar")
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        for tag, color in (("info", MUTED), ("ok", FG), ("warn", WARN), ("err", ERR)):
            self.log.tag_configure(tag, foreground=color)

    def _tree(self, parent, cols, show, tree_head=None):
        fr = tk.Frame(parent, bg=BG)
        fr.pack(fill="both", expand=True)
        t = ttk.Treeview(fr, columns=[c[0] for c in cols], show=show, style="Vault.Treeview", selectmode="extended")
        if "tree" in show:
            t.heading("#0", text=tree_head or "", anchor="w")
            t.column("#0", width=560, stretch=True)
        for cid, head, w, anchor in cols:
            t.heading(cid, text=head, anchor=anchor)
            t.column(cid, width=w, anchor=anchor, stretch=(cid == "path"))
        sb = ttk.Scrollbar(fr, orient="vertical", command=t.yview, style="Vault.Vertical.TScrollbar")
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        for tag, color in (("seguro", FG), ("revisar", WARN), ("vault", CYAN), ("muted", MUTED), ("item", TEXT)):
            t.tag_configure(tag, foreground=color)
        t.bind("<<TreeviewSelect>>", self._on_select)
        t.bind("<Double-1>", lambda e: self._open_paths(self._selected_paths()))
        return t

    # ── pestaña GUARDA MANUAL
    def _build_vault(self, p, dnd_files):
        pad = dict(padx=4)
        self._label(p, "DESTINO (tu HDD externo)", FG, bold=True).pack(fill="x", pady=(10, 2), **pad)
        drow = tk.Frame(p, bg=BG)
        drow.pack(fill="x", **pad)
        tk.Label(drow, textvariable=self.dest, bg=PANEL, fg=TEXT, font=(MONO, 10), anchor="w",
                 padx=8, pady=5).pack(side="left", fill="x", expand=True)
        self._btn(drow, "ELEGIR…", self._pick_dest).pack(side="left", padx=(8, 0))
        self._btn(drow, "LOGS", self._open_logs).pack(side="left", padx=(8, 0))

        hint = "ORIGEN (arrastrá carpetas acá)" if dnd_files else "ORIGEN"
        self._label(p, hint, FG, bold=True).pack(fill="x", pady=(12, 2), **pad)
        srow = tk.Frame(p, bg=BG)
        srow.pack(fill="both", expand=True, **pad)
        self.listbox = tk.Listbox(srow, height=8, bg=PANEL, fg=TEXT, font=(MONO, 10),
                                  selectbackground=DIM, selectforeground=TEXT, relief="flat",
                                  highlightthickness=1, highlightbackground=DIM, activestyle="none",
                                  selectmode="extended")
        self.listbox.pack(side="left", fill="both", expand=True)
        col = tk.Frame(srow, bg=BG)
        col.pack(side="left", padx=(8, 0), fill="y")
        for txt, cmd in (("+ CARPETA", self._add_dir), ("+ ARCHIVOS", self._add_files),
                         ("QUITAR", self._remove_sel), ("VACIAR LISTA", self._clear)):
            self._btn(col, txt, cmd).pack(fill="x", pady=(0, 4))
        if dnd_files:
            self.listbox.drop_target_register(dnd_files)
            self.listbox.dnd_bind("<<Drop>>", self._on_drop)

        frow = tk.Frame(p, bg=BG)
        frow.pack(fill="x", pady=(12, 0), **pad)
        self._label(frow, "FILTRO: solo archivos sin modificar hace", TEXT, 10).pack(side="left")
        ttk.Combobox(frow, textvariable=self.age, values=[a for a, _ in AGE_OPTIONS], state="readonly",
                     width=10, style="Vault.TCombobox", font=(MONO, 10)).pack(side="left", padx=8)
        tk.Checkbutton(frow, text="guardar con ruta completa (" + ("/Users/…" if IS_MAC else "C\\Users\\…") + ")", variable=self.full_path, bg=BG, fg=TEXT,
                       selectcolor=PANEL, activebackground=BG, activeforeground=FG, font=(MONO, 10),
                       highlightthickness=0, cursor="hand2").pack(side="left", padx=(12, 0))

        mrow = tk.Frame(p, bg=BG)
        mrow.pack(fill="x", pady=(8, 0), **pad)
        for val, txt in ((MODE_MOVE, "MOVER AL HDD (libera espacio)"), (MODE_BACKUP, "SOLO COPIA (no borra nada)")):
            tk.Radiobutton(mrow, text=txt, value=val, variable=self.mode, bg=BG, fg=TEXT,
                           selectcolor=PANEL, activebackground=BG, activeforeground=FG,
                           font=(MONO, 10), highlightthickness=0, cursor="hand2").pack(side="left", padx=(0, 18))

        arow = tk.Frame(p, bg=BG)
        arow.pack(fill="x", pady=(12, 6), **pad)
        self.b_scan = self._btn(arow, "ESCANEAR", self._scan)
        self.b_scan.pack(side="left")
        self.b_run = self._btn(arow, "▶ EJECUTAR", self._run, accent=True)
        self.b_run.pack(side="left", padx=8)
        self.b_cancel = self._btn(arow, "■ CANCELAR", self._cancel)
        self.b_cancel.pack(side="left")
        self.b_cancel.configure(state="disabled")

    # ── pestaña AUTO (Programador de tareas)
    def _build_auto(self, p):
        pad = dict(padx=4)
        self._label(p, "TAREAS PROGRAMADAS", FG, bold=True).pack(fill="x", pady=(10, 2), **pad)
        self._label(p, f"Corren solas con {SCHEDULER}, aunque la app esté cerrada. "
                       "Usan la CARPETA de INICIO y el HDD que tengas elegidos al programar. "
                       + ("Si la Mac estaba dormida a esa hora, corren apenas se despierta (apagada, no)."
                          if IS_MAC else "Si la PC estaba apagada a esa hora, corren apenas se prende.")).pack(fill="x", **pad)
        if not (IS_WIN or IS_MAC):
            self._label(p, "Solo disponible en Windows y Mac.", WARN).pack(fill="x", pady=8, **pad)
            return
        saved = self.cfg.get("auto", {})
        extra = {"limpieza": "En automático NUNCA vacía la papelera (es tu red de seguridad), y las caches "
                             f"usadas en los últimos {CACHE_QUIET_DAYS} días se saltean (por si VS Code o un "
                             "proyecto está abierto).",
                 "sesiones": "Si el HDD no está conectado a esa hora, no hace nada. Si algo da un aviso "
                             "(OneDrive, mismo disco), ese elemento se saltea."}
        for key, pr in PRESETS.items():
            box = tk.Frame(p, bg=PANEL, highlightthickness=1, highlightbackground=DIM)
            box.pack(fill="x", pady=(10, 0), **pad)
            tk.Label(box, text=pr["title"].upper(), bg=PANEL, fg=FG, font=(MONO, 10, "bold"),
                     anchor="w").pack(fill="x", padx=10, pady=(8, 0))
            tk.Label(box, text=pr["desc"] + " " + extra[key], bg=PANEL, fg=MUTED, font=(MONO, 9),
                     anchor="w", justify="left", wraplength=860).pack(fill="x", padx=10)
            row = tk.Frame(box, bg=PANEL)
            row.pack(fill="x", padx=10, pady=(6, 4))
            when0, hour0 = AUTO_DEFAULTS[key]
            when = tk.StringVar(value=saved.get(key, {}).get("when", when0))
            hour = tk.StringVar(value=saved.get(key, {}).get("hour", hour0))
            ttk.Combobox(row, textvariable=when, values=[w for w, _ in WHEN_OPTIONS], state="readonly",
                         width=20, style="Vault.TCombobox", font=(MONO, 10)).pack(side="left")
            tk.Label(row, text="a las", bg=PANEL, fg=TEXT, font=(MONO, 10)).pack(side="left", padx=6)
            ttk.Combobox(row, textvariable=hour, values=HOURS, state="readonly", width=6,
                         style="Vault.TCombobox", font=(MONO, 10)).pack(side="left")
            b_on = self._btn(row, "PROGRAMAR", lambda k=key: self._schedule(k), accent=True)
            b_on.pack(side="left", padx=(12, 0))
            b_off = self._btn(row, "QUITAR", lambda k=key: self._unschedule(k))
            b_off.pack(side="left", padx=(8, 0))
            b_now = self._btn(row, "PROBAR AHORA", lambda k=key: self._run_task_now(k))
            b_now.pack(side="left", padx=(8, 0))
            status = tk.StringVar(value="")
            tk.Label(box, textvariable=status, bg=PANEL, fg=TEXT, font=(MONO, 9), anchor="w",
                     justify="left", wraplength=860).pack(fill="x", padx=10, pady=(0, 8))
            self.auto_ui[key] = dict(when=when, hour=hour, status=status, on=b_on, off=b_off, now=b_now)

        hrow = tk.Frame(p, bg=BG)
        hrow.pack(fill="x", pady=(12, 2), **pad)
        self._label(hrow, "ÚLTIMAS CORRIDAS", FG, bold=True).pack(side="left")
        self._btn(hrow, "ACTUALIZAR", self._refresh_auto).pack(side="right")
        self._btn(hrow, "LOGS", lambda: open_folder(str(DELETE_LOG_DIR)) if DELETE_LOG_DIR.is_dir()
                  else messagebox.showinfo(APP_NAME, "Todavía no hay logs.")).pack(side="right", padx=8)
        self.auto_log = tk.Text(p, height=8, bg="#050805", fg=TEXT, font=(MONO, 9), relief="flat", wrap="word",
                                highlightthickness=1, highlightbackground=DIM, padx=8, pady=6, state="disabled")
        self.auto_log.pack(fill="both", expand=True, pady=(0, 6), **pad)

    def _refresh_auto(self):
        if not self.auto_ui:
            return
        saved = self.cfg.get("auto", {})
        for key, ui in self.auto_ui.items():
            info = saved.get(key)
            if task_exists(key):
                txt = "● PROGRAMADO"
                if info:
                    txt += f" · {info['when']} a las {info['hour']} · analiza {info['root']}"
                    if PRESETS[key]["action"] == "vault":
                        txt += f" → {info.get('dest') or '?'}"
                ui["status"].set(txt)
                ui["off"].configure(state="normal")
                ui["now"].configure(state="normal")
            else:
                ui["status"].set("○ no programado" + (f" (se borró desde {OS_NAME})" if info else ""))
                ui["off"].configure(state="disabled")
                ui["now"].configure(state="disabled")
        self.auto_log.configure(state="normal")
        self.auto_log.delete("1.0", "end")
        self.auto_log.insert("end", "\n".join(read_auto_log()) or "todavía no corrió ninguna tarea programada.")
        self.auto_log.see("end")
        self.auto_log.configure(state="disabled")

    def _schedule(self, key):
        ui, pr = self.auto_ui[key], PRESETS[key]
        root = self.an_root.get()
        dest = self.dest.get() if pr["action"] == "vault" else ""
        if not os.path.isdir(root):
            return messagebox.showerror(APP_NAME, f"La carpeta a analizar no existe:\n{root}")
        if pr["action"] == "vault":
            if not dest:
                return messagebox.showerror(APP_NAME, "Elegí primero el HDD (botón CAMBIAR en INICIO).")
            if is_inside(dest, root) or is_inside(root, dest):
                return messagebox.showerror(APP_NAME, "El destino en el HDD no puede estar adentro de la carpeta "
                                                      "analizada (ni al revés).")
        when, hour = ui["when"].get(), ui["hour"].get()
        if pr["action"] == "delete":
            what = ("BORRAR PARA SIEMPRE, sin preguntar:\n  · temporales con más de 1 día\n"
                    f"  · caches que no se usaron en los últimos {CACHE_QUIET_DAYS} días\n"
                    "  · node_modules/.venv de proyectos quietos hace más de 30 días\n"
                    "  · instaladores e ISOs de Descargas con más de 30 días\n\n"
                    "Nunca vacía la papelera ni toca lo marcado REVISAR, lo del HDD o nada protegido.")
        else:
            what = ("MOVER al HDD, sin preguntar, los proyectos de DAW/video y la media pesada sin tocar "
                    f"hace más de 6 meses.\nDestino: {dest}\n\nCada archivo se borra solo si su copia "
                    "verificó con SHA-256. Si el HDD no está conectado, no hace nada.")
        if not messagebox.askyesno(APP_NAME, f"{pr['title']} · {when} a las {hour}\n"
                                             f"Carpeta: {root}\n\nVa a {what}\n\n¿Lo programo?"):
            return
        try:
            ok, msg = schedule_task(key, when, hour, root, dest)
        except OSError as e:
            ok, msg = False, str(e)
        if not ok:
            self._write("err", f"no pude programar «{pr['title']}»: {msg}")
            return messagebox.showerror(APP_NAME, f"{OS_NAME} no aceptó la tarea:\n\n{msg}")
        self.cfg.setdefault("auto", {})[key] = dict(when=when, hour=hour, root=root, dest=dest)
        save_config(self.cfg)
        self._write("ok", f"programado: {pr['title']} · {when} a las {hour} · {root}" + (f" → {dest}" if dest else ""))
        self._refresh_auto()

    def _unschedule(self, key):
        pr = PRESETS[key]
        if not messagebox.askyesno(APP_NAME, f"¿Saco «{pr['title']}» de las tareas programadas?"):
            return
        ok, msg = unschedule_task(key)
        self.cfg.get("auto", {}).pop(key, None)
        save_config(self.cfg)
        self._write("ok" if ok else "err", f"«{pr['title']}» ya no está programado" if ok else f"no pude quitarlo: {msg}")
        self._refresh_auto()

    def _run_task_now(self, key):
        pr = PRESETS[key]
        if not messagebox.askyesno(APP_NAME, f"Corro «{pr['title']}» ya mismo, en segundo plano y SIN PREGUNTAR "
                                             f"más (igual que cuando la dispara {OS_NAME}).\n\n¿Dale?", icon="warning"):
            return
        ok, msg = run_task_now(key)
        self._write("ok" if ok else "err", f"«{pr['title']}» lanzada. El resultado aparece en ÚLTIMAS CORRIDAS "
                                           "cuando termine (ACTUALIZAR)." if ok else f"no pude lanzarla: {msg}")

    # ── animaciones y números grandes
    def _blink(self):
        cur = self.cursor.cget("fg")
        self.cursor.configure(fg=BG if cur == FG else FG)
        self.root.after(530, self._blink)

    def _tween(self, key, start, end, fn, ms=700):
        """Anima un número de start a end (frena al final)."""
        if key in self._anims:
            self.root.after_cancel(self._anims.pop(key))
        steps = max(1, ms // 30)

        def step(i):
            t = i / steps
            fn(start + (end - start) * (1 - (1 - t) ** 3))
            if i < steps:
                self._anims[key] = self.root.after(30, step, i + 1)
            else:
                self._anims.pop(key, None)
        step(1)

    def _set_hero(self, key, value, animate=True):
        def put(v):
            self.hero[key] = v
            self.hero_lbl[key].configure(text=human(v))
        if animate:
            self._tween(f"hero:{key}", self.hero[key], float(value), put)
        else:
            put(float(value))

    def _set_total(self, tot):
        self.total_var.set(f"liberado en total: {human(tot)}" if tot else "")

    def _update_dest_txt(self):
        d = self.dest.get()
        if not d:
            self.dest_txt.set("HDD: sin elegir")
        else:
            self.dest_txt.set(f"HDD: {d}" + ("" if os.path.isdir(d) else "  (no conectado)"))

    def _refresh_disk(self, animate=True):
        root = self.an_root.get()
        try:
            u = shutil.disk_usage(root)
        except OSError:
            self.disk = None
            self.hero_cap["free"].configure(text="libres")
            self._draw_disk()
            return
        drive = volume_of(root)
        self.disk = (drive, u.total, u.used, u.free)
        self.hero_cap["free"].configure(text=f"libres en {volume_label(drive)} de {human(u.total)}")
        self._set_hero("free", u.free, animate)
        self._draw_disk()

    def _draw_disk(self):
        c = self.diskc
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 10:
            return
        c.create_rectangle(0, 0, w, h, fill=PANEL, outline=DIM)
        if self.disk:
            _drive, total, used, free = self.disk
            clean, keep = self._totals(same_drive=True)
            xu = w * used / total
            c.create_rectangle(0, 0, xu, h, fill=DISK_USED, outline="")
            if self._scan_phase is None:
                wk, wc = w * min(keep, used) / total, w * min(clean, used) / total
                c.create_rectangle(xu - wc - wk, 0, xu - wc, h, fill=CYAN, outline="")
                c.create_rectangle(xu - wc, 0, xu, h, fill=FG, outline="")
            self.leg["used"].configure(text=f"usado {human(used)}")
            self.leg["clean"].configure(text=f"limpiable {human(clean)}")
            self.leg["keep"].configure(text=f"al HDD {human(keep)}")
            self.leg["free"].configure(text=f"libre {human(free)}")
        if self._scan_phase is not None:  # barrido mientras analiza
            x = (self._scan_phase * 14) % (w + 120) - 120
            for i in range(6):
                c.create_rectangle(x + i * 20, 2, x + i * 20 + 16, h - 2, fill=FG if i == 5 else DIM, outline="")
        for x in range(8, w, 8):  # bloques retro
            c.create_line(x, 0, x, h, fill=BG, width=2)
        if self._flash and time.time() < self._flash[1]:
            c.create_text(w / 2, h / 2, text=self._flash[0], fill="#ffffff", font=(MONO, 11, "bold"))

    def _scan_anim(self):
        if self._scan_phase is None:
            return
        self._scan_phase += 1
        self._draw_disk()
        self.root.after(40, self._scan_anim)

    def _flash_msg(self, text, secs=5):
        self._flash = (text, time.time() + secs)
        self._draw_disk()
        self.root.after(int(secs * 1000) + 50, self._draw_disk)

    # ── tarjetas
    def _card_on(self, rec):
        return self.cards_on.get(rec.key, CARD_DEFAULT[rec.safety])

    def _card_items(self, rec):
        """Lo que la tarjeta realmente toca: las caches en uso quedan afuera."""
        if rec.key != "cache":
            return rec.items, 0
        cut = time.time() - CACHE_QUIET_DAYS * DAY
        keep = [i for i in rec.items if i.mtime <= cut]
        return keep, len(rec.items) - len(keep)

    def _totals(self, same_drive=False):
        clean = keep = 0
        if not self.report:
            return 0, 0
        drive = self.disk[0] if (same_drive and self.disk) else None
        for rec in self.report.recs:
            if not self._card_on(rec):
                continue
            for it in self._card_items(rec)[0]:
                if drive and it.path != BIN_PATH and volume_of(it.path) != drive:
                    continue
                if rec.action == "vault":
                    keep += it.size
                else:
                    clean += it.size
        return clean, keep

    def _render_cards(self):
        for w in self.cards_box.winfo_children():
            w.destroy()
        self.cards.clear()
        rep = self.report
        if self._scan_phase is not None:
            self._label(self.cards_box, var=self.status, color=TEXT).pack(fill="x", pady=20, padx=8)
            return
        if not rep:
            self._label(self.cards_box, "Dale a ▶ ANALIZAR. Reviso la carpeta SIN TOCAR NADA y te muestro qué se puede "
                                        "limpiar y qué conviene guardar en el HDD.", TEXT, 10).pack(
                fill="x", pady=20, padx=8)
            return
        if not rep.recs:
            self._label(self.cards_box, "✔ todo en orden: no encontré nada para limpiar ni para guardar.", FG, 11,
                        True).pack(fill="x", pady=20, padx=8)
            return
        for rec in rep.recs:
            self._make_card(rec)

    def _make_card(self, rec):
        color = SAFETY_COLOR[rec.safety]
        f = tk.Frame(self.cards_box, bg=PANEL, highlightthickness=1, cursor="hand2")
        f.pack(fill="x", pady=(0, 6))
        top = tk.Frame(f, bg=PANEL)
        top.pack(fill="x", padx=10, pady=(6, 0))
        chk = tk.Label(top, bg=PANEL, font=(MONO, 12, "bold"))
        chk.pack(side="left")
        tag = tk.Label(top, text=SAFETY_TAG[rec.safety], bg=PANEL, fg=color, font=(MONO, 9, "bold"), width=8)
        tag.pack(side="left", padx=(6, 6))
        title = tk.Label(top, text=rec.title, bg=PANEL, font=(MONO, 10, "bold"), anchor="w")
        title.pack(side="left", fill="x", expand=True)
        more = tk.Label(top, text="ver ›", bg=PANEL, fg=MUTED, font=(MONO, 9))
        more.pack(side="right", padx=(10, 0))
        items, skipped = self._card_items(rec)
        size = tk.Label(top, text=human(sum(i.size for i in items)), bg=PANEL, font=(MONO, 12, "bold"))
        size.pack(side="right")
        count = f"{len(items)} ítem{'s' if len(items) != 1 else ''}"
        if skipped:
            count += f" · {skipped} en uso, se saltean"
        if rec.key == "dups":
            same = sum(1 for i in items if i.ref and same_folder(i.path, i.ref))
            count += f" · {same} en la misma carpeta que el original · {len(items) - same} en otra carpeta"
        why = rec.why.split(". ")[0].rstrip(".") + "."
        sub = tk.Label(f, text=f"{count}  ·  {why}", bg=PANEL, fg=MUTED, font=(MONO, 8), anchor="w",
                       justify="left", wraplength=820)
        sub.pack(fill="x", padx=10, pady=(0, 6))
        self.cards[rec.key] = dict(frame=f, chk=chk, title=title, size=size, color=color)
        for w in (f, top, chk, tag, title, size, sub):
            w.bind("<Button-1>", lambda e, k=rec.key: self._toggle_card(k))
            w.bind("<Enter>", lambda e, k=rec.key: self._hover_card(k, True))
            w.bind("<Leave>", lambda e, k=rec.key: self._hover_card(k, False))
        more.bind("<Button-1>", lambda e, k=rec.key: self._show_detail(k))
        more.bind("<Enter>", lambda e: more.configure(fg=TEXT))
        more.bind("<Leave>", lambda e: more.configure(fg=MUTED))
        self._style_card(rec)

    def _style_card(self, rec, hover=False):
        c = self.cards.get(rec.key)
        if not c:
            return
        on = self._card_on(rec)
        c["chk"].configure(text="[■]" if on else "[ ]", fg=c["color"] if on else MUTED)
        c["title"].configure(fg=TEXT if on else MUTED)
        c["size"].configure(fg=c["color"] if on else MUTED)
        c["frame"].configure(highlightbackground=TEXT if hover else (c["color"] if on else DIM))

    def _hover_card(self, key, on):
        rec = next((r for r in (self.report.recs if self.report else []) if r.key == key), None)
        if rec:
            self._style_card(rec, hover=on)

    def _toggle_card(self, key):
        if self._is_busy or not self.report:
            return
        rec = next((r for r in self.report.recs if r.key == key), None)
        if not rec:
            return
        self.cards_on[key] = not self._card_on(rec)
        self.cfg["cards"] = self.cards_on
        save_config(self.cfg)
        self._style_card(rec, hover=True)
        self._update_totals()

    def _show_detail(self, key):
        self.nb.select(self.tab_detail)
        self.anb.select(self.anb.tabs()[0])
        iid = f"r:{key}"
        if self.t_rec.exists(iid):
            self.t_rec.item(iid, open=True)
            self.t_rec.selection_set(iid)
            self.t_rec.see(iid)

    def _update_totals(self, animate=True):
        clean, keep = self._totals()
        if self._scan_phase is not None:  # analizando: todavía no hay números
            clean = keep = 0
            for k in ("clean", "keep"):
                self.hero_lbl[k].configure(text="···")
        elif self.report:
            self._set_hero("clean", clean, animate)
            self._set_hero("keep", keep, animate)
        else:
            for k in ("clean", "keep"):
                self.hero[k] = 0.0
                self.hero_lbl[k].configure(text="—")
        self.b_clean.configure(text=f"✖ LIMPIAR {human(clean)}" if clean else "✖ LIMPIAR")
        self.b_keep.configure(text=f"→ GUARDAR {human(keep)} EN EL HDD" if keep else "→ GUARDAR EN EL HDD")
        self._set_btn(self.b_clean, bool(clean) and not self._is_busy)
        self._set_btn(self.b_keep, bool(keep) and not self._is_busy)
        self._draw_disk()

    @staticmethod
    def _set_btn(b, on):
        bg, fg = b._colors
        b.configure(state="normal" if on else "disabled", bg=bg if on else PANEL, fg=fg if on else MUTED)

    # ── acciones de INICIO
    def _clean(self):
        if not self.report:
            return
        paths = []
        for rec in self.report.recs:
            if rec.action != "vault" and self._card_on(rec):
                paths += [i.path for i in self._card_items(rec)[0]]
        self._delete_flow(paths)

    def _keep(self):
        if not self.report:
            return
        paths = []
        for rec in self.report.recs:
            if rec.action == "vault" and self._card_on(rec):
                paths += [i.path for i in rec.items if os.path.exists(i.path)]
        if not paths:
            return
        if not (self.dest.get() and os.path.isdir(self.dest.get())):
            messagebox.showinfo(APP_NAME, "Elegí el HDD externo (o una carpeta adentro, tipo "
                                          + ("/Volumes/HDD/Guardado)." if IS_MAC else "E:\\Guardado)."))
            self._pick_dest()
            if not (self.dest.get() and os.path.isdir(self.dest.get())):
                return
        saved = list(self.sources)
        self.sources = []
        self._prep_vault(paths)
        self._saved_sources = saved
        if not self._run():  # cancelaste o el chequeo previo frenó: vuelve tu lista manual
            self.sources, self._saved_sources = saved, None
            self._refresh_list()

    # ── origen / destino
    def _age_days(self):
        return dict(AGE_OPTIONS).get(self.age.get(), 0)

    def _add_source(self, p):
        p = os.path.abspath(p)
        if any(is_inside(p, s) for s in self.sources):
            return
        self.sources = [s for s in self.sources if not is_inside(s, p)]  # si agrego el padre, saco los hijos
        self.sources.append(p)
        self._refresh_list()

    def _refresh_list(self):
        self.listbox.delete(0, "end")
        for s in self.sources:
            self.listbox.insert("end", ("▣ " if os.path.isdir(s) else "· ") + s)

    def _add_dir(self):
        d = filedialog.askdirectory(title="Carpeta a guardar en el HDD")
        if d:
            self._add_source(d)

    def _add_files(self):
        for f in filedialog.askopenfilenames(title="Archivos a guardar en el HDD"):
            self._add_source(f)

    def _on_drop(self, event):
        for p in self.root.tk.splitlist(event.data):
            self._add_source(p)

    def _remove_sel(self):
        idx = set(self.listbox.curselection())
        self.sources = [s for i, s in enumerate(self.sources) if i not in idx]
        self._refresh_list()

    def _clear(self):
        self.sources = []
        self._refresh_list()

    def _pick_dest(self):
        d = filedialog.askdirectory(title="Elegí el HDD externo (o una carpeta adentro)")
        if d:
            self.dest.set(os.path.abspath(d))
            self.cfg["dest"] = self.dest.get()
            save_config(self.cfg)
        self._update_dest_txt()

    def _pick_an_root(self):
        d = filedialog.askdirectory(title="¿Qué analizo? (tu usuario o un disco)", initialdir=self.an_root.get())
        if d:
            self.an_root.set(os.path.abspath(d))
            self.cfg["an_root"] = self.an_root.get()
            save_config(self.cfg)
            if self.report and _norm(self.report.root) != _norm(self.an_root.get()):
                self.report = None  # el análisis era de otra carpeta
                self._render_report()
            self._refresh_disk()

    def _open_logs(self):
        d = Path(self.dest.get() or ".")
        p = next((x for x in (d / LOG_DIR, d / LEGACY_LOG_DIR) if x.is_dir()), None)
        if p:
            open_folder(str(p))
        else:
            messagebox.showinfo(APP_NAME, "Todavía no hay logs en ese destino.")

    # ── estado
    def _busy(self, on):
        self._is_busy = on
        for b in self.run_buttons:
            if b in (self.b_clean, self.b_keep):
                continue
            b.configure(state="disabled" if on else "normal")
        for b in self.cancel_buttons:
            b.configure(state="normal" if on else "disabled")
        self._update_totals(animate=False)

    def _start(self, target, *args):
        self.cancel.clear()
        self._busy(True)
        self.worker = threading.Thread(target=target, args=args, daemon=True)
        self.worker.start()

    # ── acciones GUARDA (vault)
    def _scan(self):
        if not self.sources:
            return messagebox.showwarning(APP_NAME, "Agregá algo primero.")
        self.status.set("escaneando…")
        days = self._age_days()

        def work():
            try:
                files, _d, total, errs, recent = scan(self.sources, self.cancel, days)
                self.q.put(("scanned", len(files), total, len(errs), recent))
            except Cancelled:
                self.q.put(("scanned", None, 0, 0, 0))

        self._start(work)

    def _run(self) -> bool:
        """Chequea, confirma y arranca. Devuelve True si arrancó."""
        mode = self.mode.get()
        errors, warnings = preflight(self.sources, self.dest.get(), mode)
        if errors:
            messagebox.showerror(APP_NAME, "\n\n".join(errors))
            return False
        if warnings and not messagebox.askyesno(APP_NAME, "\n\n".join(warnings) + "\n\n¿Sigo igual?"):
            return False
        days = self._age_days()
        filt = f"\n\nFiltro: solo archivos sin modificar hace más de {days} días." if days else ""
        n = len(self.sources)
        if mode == MODE_MOVE and not messagebox.askyesno(
                APP_NAME,
                f"Vas a GUARDAR {n} elemento(s) en el HDD:\n{self.dest.get()}\n\n"
                "Cada archivo se copia, se verifica con SHA-256 y SOLO si coincide se borra el original "
                "(definitivo, no va a la papelera)." + filt + "\n\n¿Dale?"):
            return False
        self.cfg.update(dest=self.dest.get(), mode=mode, age=self.age.get(), full_path=self.full_path.get())
        save_config(self.cfg)
        self.bar.configure(mode="determinate")
        self.bar["value"] = 0
        self._write("ok", f"── arranca: {'MOVER' if mode == MODE_MOVE else 'COPIA'} → {self.dest.get()}"
                          + (f" · filtro +{days} días" if days else ""))
        emit = lambda *a: self.q.put(a)  # noqa: E731
        self._start(run_job, list(self.sources), self.dest.get(), mode, self.cancel, emit, days, self.full_path.get())
        return True

    def _cancel(self):
        self.cancel.set()
        self.status.set("cancelando… (termino el archivo actual de forma segura)")

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_NAME, "Hay una operación en curso. ¿Cancelar y salir?"):
                return
            self.cancel.set()
            self.worker.join(timeout=5)
        self.root.destroy()

    # ── acciones ANÁLISIS
    def _analyze(self):
        root = self.an_root.get()
        if not os.path.isdir(root):
            return messagebox.showerror(APP_NAME, f"No existe: {root}")
        self.cfg["an_root"] = root
        save_config(self.cfg)
        self.bar.configure(mode="indeterminate")
        self.bar.start(12)
        self._write("ok", f"── análisis de {root}")
        self.nb.select(self.tab_home)
        self._refresh_disk(animate=False)
        self._scan_phase = 0
        self._render_cards()
        for k in ("clean", "keep"):
            self.hero_lbl[k].configure(text="···")
        self._scan_anim()

        def work():
            try:
                rep = analyze(root, self.cancel, lambda m: self.q.put(("current", m)))
                self.q.put(("an_done", rep))
            except Cancelled:
                self.q.put(("an_done", None))
            except Exception as e:  # noqa: BLE001
                self.q.put(("log", "err", f"Error en el análisis: {e}"))
                self.q.put(("an_done", None))

        self._start(work)

    def _render_report(self, keep_zoom=False):
        rep = self.report
        for t in self.trees:
            t.delete(*t.get_children())
        self.iid_paths.clear()
        self.iid_hint.clear()
        self.path_info.clear()
        self.map.set_tree(rep.tree if rep else None, keep_zoom)
        self._render_cards()
        self._update_totals()
        if not rep:
            self.an_summary.set("todavía no analizaste nada. arrancá en INICIO.")
            return
        for rec in rep.recs:
            pid = f"r:{rec.key}"
            self.t_rec.insert("", "end", iid=pid, text=f"{BADGE[rec.safety]} {rec.title}",
                              values=(human(rec.size), len(rec.items)), tags=(rec.safety,))
            self.iid_paths[pid] = [i.path for i in rec.items]
            self.iid_hint[pid] = rec.why
            groups, gsize = {}, defaultdict(int)
            for j, it in enumerate(rec.items):
                self.path_info[it.path] = (it.size, rec.safety)
                if j >= MAX_ROWS:
                    continue
                iid = f"r:{rec.key}:{j}"
                parent = pid
                label = f"{BIN_NAME} ({it.note})" if it.path == BIN_PATH else it.path
                if rec.key == "dups" and it.ref:  # duplicados: agrupados bajo el original que se conserva
                    gid = groups.get(it.ref)
                    if gid is None:
                        gid = groups[it.ref] = f"r:dups:g{len(groups)}"
                        self.t_rec.insert(pid, "end", iid=gid, open=True, tags=("muted",), values=("", ""),
                                          text=f"✔ se conserva: {it.ref}")
                        self.iid_paths[gid] = []  # elegir el grupo = elegir sus copias, nunca el original
                        self.iid_hint[gid] = f"original que se conserva (no se toca): {it.ref}"
                    self.iid_paths[gid].append(it.path)
                    gsize[gid] += it.size
                    parent = gid
                    label = f"{it.path}   ·   {'MISMA carpeta' if same_folder(it.path, it.ref) else 'OTRA carpeta'}"
                self.t_rec.insert(parent, "end", iid=iid, text=label, values=(human(it.size), ""), tags=("item",))
                self.iid_paths[iid] = [it.path]
                self.iid_hint[iid] = it.note
            for gid, s in gsize.items():
                self.t_rec.item(gid, values=(human(s), len(self.iid_paths[gid])))
            if len(rec.items) > MAX_ROWS:
                self.t_rec.insert(pid, "end", iid=f"r:{rec.key}:more", tags=("muted",), values=("", ""),
                                  text=f"… y {len(rec.items) - MAX_ROWS} más (seleccioná la categoría para incluirlos)")
        for i, (size, mt, path, sysf) in enumerate(rep.top_files):
            iid = f"f:{i}"
            self.t_files.insert("", "end", iid=iid, values=(human(size), age_txt(mt), path),
                                tags=("muted",) if sysf else ("item",))
            self.iid_paths[iid] = [path]
            self.iid_hint[iid] = ("archivo de programa/sistema: borralo solo si sabés qué es" if sysf
                                  else f"modificado por última vez hace {age_txt(mt)}")
            self.path_info.setdefault(path, (size, "sistema" if sysf else "manual"))
        tot = rep.total or 1
        for i, (p, s, kids) in enumerate(rep.top_dirs):
            iid = f"d:{i}"
            self.t_dirs.insert("", "end", iid=iid, text=os.path.basename(p) or p,
                               values=(human(s), f"{s * 100 / tot:.1f}"), tags=("item",))
            self.iid_paths[iid] = [p]
            self.iid_hint[iid] = p
            self.path_info.setdefault(p, (s, "manual"))
            for j, (q, t) in enumerate(kids):
                cid = f"d:{i}:{j}"
                self.t_dirs.insert(iid, "end", iid=cid, text=os.path.basename(q),
                                   values=(human(t), f"{t * 100 / tot:.1f}"), tags=("muted",))
                self.iid_paths[cid] = [q]
                self.iid_hint[cid] = q
                self.path_info.setdefault(q, (t, "manual"))
        safe, rev, vault = rep.by_safety("seguro"), rep.by_safety("revisar"), rep.by_safety("vault")
        extra = f" · {rep.cloud} solo-en-la-nube ignorados" if rep.cloud else ""
        self.an_summary.set(f"{human(rep.total)} en {rep.n_files:,} archivos · ".replace(",", ".")
                            + f"seguro: {human(safe)} · a revisar: {human(rev)} · "
                            f"para el HDD: {human(vault)}{extra}")
        self.an_hint.set("seleccioná algo para ver por qué está recomendado.")

    def _on_select(self, event):
        t = event.widget
        sel = t.selection()
        if not sel:
            return
        tot = sum(self.path_info.get(p, (0,))[0] for p in self._selected_paths(t))
        hint = self.iid_hint.get(t.focus() or sel[0], "")
        self.an_hint.set(f"selección: {human(tot)}  ·  {hint}")

    def _selected_paths(self, tree=None):
        tree = tree or self.trees[self.anb.index(self.anb.select())]
        seen, out = set(), []
        for iid in tree.selection():
            for p in self.iid_paths.get(iid, []):
                if p not in seen:
                    seen.add(p)
                    out.append(p)
        dirs = sorted((p for p in out if p != BIN_PATH and os.path.isdir(p)), key=len)
        if dirs:  # si elegiste una carpeta y algo de adentro, queda solo la carpeta
            keep = []
            for p in out:
                if p != BIN_PATH and any(d != p and is_inside(p, d) for d in dirs):
                    continue
                keep.append(p)
            out = keep
        return out

    def _map_paths(self):
        n = self.map.sel
        return [n.path] if n is not None and n.path else []

    def _prep_vault(self, paths):
        """Carga rutas en GUARDA con la configuración correcta para lo que viene del análisis."""
        for p in paths:
            self._add_source(p)
        self.full_path.set(True)
        self.mode.set(MODE_MOVE)
        if self._age_days():  # con filtro, un proyecto podría quedar mitad acá y mitad en el HDD
            self.age.set(AGE_OPTIONS[0][0])
            self._write("info", "filtro puesto en 'todo': lo que viene del análisis ya está filtrado por antigüedad "
                                "y los proyectos tienen que viajar enteros.")

    def _send_paths(self, paths):
        paths = [p for p in paths if p != BIN_PATH and os.path.exists(p)]
        if not paths:
            return messagebox.showinfo(APP_NAME, "Seleccioná qué guardar en el HDD.")
        self._prep_vault(paths)
        self.nb.select(self.tab_v)
        self._write("ok", f"{len(paths)} elemento(s) cargados en GUARDA MANUAL con ruta completa. "
                          "Revisá y dale a EJECUTAR.")

    def _delete_flow(self, paths):
        """Confirmación única con todos los avisos, y después borra en segundo plano."""
        if not paths:
            return messagebox.showinfo(APP_NAME, "Seleccioná qué borrar.")
        has_bin = BIN_PATH in paths
        paths = [p for p in paths if p != BIN_PATH]
        protected = [p for p in paths if is_protected(p)]
        if protected:
            return messagebox.showerror(APP_NAME, "Esto no lo borro nunca (sistema, raíz o carpeta de usuario):\n\n"
                                        + "\n".join(protected[:8]))
        bin_size = self.path_info.get(BIN_PATH, (0,))[0] if has_bin else 0
        size = sum(self.path_info.get(p, (0,))[0] for p in paths) + bin_size
        kinds = {self.path_info.get(p, (0, "manual"))[1] for p in paths}
        warn = ""
        if "vault" in kinds:
            warn += "\n⚠ Hay cosas recomendadas para GUARDAR en el HDD, no para borrar. Si las borrás, no vuelven.\n"
        if "revisar" in kinds:
            warn += "\n⚠ Hay cosas marcadas para REVISAR. Asegurate de que no sean lo único que tenés.\n"
        if "sistema" in kinds or any(f"{os.sep}AppData{os.sep}" in p for p in paths
                                     if self.path_info.get(p, (0, ""))[1] != "seguro"):
            warn += "\n⚠ Hay archivos de programas (AppData/sistema): borrarlos puede romper apps.\n"
        lines = []
        if has_bin:
            lines.append(f"  · vaciar la {BIN_NAME.lower()} ({human(bin_size)})")
        lines += ["  " + (p if len(p) < 80 else "…" + p[-78:]) for p in paths[:6]]
        more = f"\n  … y {len(paths) - 6} más" if len(paths) > 6 else ""
        n = len(paths) + (1 if has_bin else 0)
        if not messagebox.askyesno(
                APP_NAME,
                f"Vas a BORRAR PARA SIEMPRE {n} elemento(s), ~{human(size)}.\n"
                f"No pasa por la papelera (así se libera el espacio de verdad).\n{warn}\n"
                + "\n".join(lines) + f"{more}\n\n¿Dale?", icon="warning"):
            return
        self.bar.configure(mode="indeterminate")
        self.bar.start(12)
        self._write("warn", f"── borrando {n} elemento(s)…")

        def work():
            if paths:
                res = delete_paths(paths, self.cancel, lambda *a: self.q.put(a))
            else:
                res = dict(freed=0, deleted=[], errors=0, log_path="")
            if has_bin and not self.cancel.is_set():
                if empty_recycle_bin():
                    res["bin"] = True
                    res["freed"] += bin_size
                else:
                    res["errors"] += 1
                    self.q.put(("log", "err", "no pude vaciar la papelera"))
            self.q.put(("del_done", res))

        self._start(work)

    def _open_paths(self, paths):
        paths = [p for p in paths if p != BIN_PATH]
        if paths and os.path.exists(paths[0]):
            reveal(paths[0])

    # ── cola de eventos del worker
    def _poll(self):
        try:
            while True:
                kind, *args = self.q.get_nowait()
                getattr(self, f"_on_{kind}")(*args)
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _write(self, level, msg):
        self.log.configure(state="normal")
        self.log.insert("end", f"{datetime.now():%H:%M:%S}  {msg}\n", level)
        if int(self.log.index("end-1c").split(".")[0]) > 5000:
            self.log.delete("1.0", "1000.0")
        self.log.see("end")
        self.log.configure(state="disabled")
        short = msg if len(msg) < 140 else msg[:137] + "…"
        self.last_msg.set(f"› {short}")
        self.last_lbl.configure(fg={"info": MUTED, "ok": FG, "warn": WARN, "err": ERR}.get(level, MUTED))

    def _on_log(self, level, msg):
        self._write(level, msg)

    def _on_current(self, text):
        self.status.set(text)

    def _stop_bar(self, full=True):
        self.bar.stop()
        self.bar.configure(mode="determinate")
        self.bar["value"] = 1000 if full else 0

    def _on_progress(self, st, elapsed):
        total = st["total_bytes"] or 1
        self.bar["value"] = min(1000, st["done_bytes"] * 1000 / total)
        speed = st["done_bytes"] / elapsed if elapsed > 0 else 0
        eta = (st["total_bytes"] - st["done_bytes"]) / speed if speed > 0 else 0
        self.stats_txt.set(f"{human(st['done_bytes'])} / {human(st['total_bytes'])} · {human(speed)}/s · "
                           f"faltan ~{int(eta // 60)}m{int(eta % 60):02d}s · liberado {human(st['freed'])} · "
                           f"errores {st['errors']}")

    def _on_scanned(self, n, total, nerr, recent):
        self._busy(False)
        if n is None:
            self.status.set("escaneo cancelado")
            return
        self.status.set("escaneo listo")
        msg = f"escaneo: {n} archivos · {human(total)}"
        if self._age_days():
            msg += f" · {recent} recientes quedan afuera por el filtro"
        self._write("info", msg + (f" · {nerr} sin acceso" if nerr else ""))
        dest = self.dest.get()
        if dest and os.path.isdir(dest):
            free = shutil.disk_usage(dest).free
            lvl = "ok" if free > total else "err"
            self._write(lvl, f"libre en destino: {human(free)}" + ("" if free > total else "  ← NO ENTRA"))

    def _forget_gone(self, gone=()):
        """Saca del análisis lo que ya no existe (borrado o movido al HDD)."""
        rep = self.report
        if not rep:
            return
        exists = lambda p: p == BIN_PATH or os.path.lexists(p)  # noqa: E731
        lost = {_norm(i.path) for r in rep.recs for i in r.items if not exists(i.path)} | {_norm(p) for p in gone}
        for r in rep.recs:
            r.items = [i for i in r.items if exists(i.path)]
        rep.recs = [r for r in rep.recs if r.items]
        rep.top_files = [f for f in rep.top_files if exists(f[2])]
        rep.top_dirs = [(p, s, [k for k in kids if exists(k[0])]) for p, s, kids in rep.top_dirs if exists(p)]
        rep.total = max(0, rep.total - prune_tree(rep.tree, lost))
        self._render_report(keep_zoom=True)

    def _after_change(self, freed):
        """Después de borrar o mover: contador histórico, disco animado y festejo."""
        if freed > 0:
            self._set_total(add_freed(freed))
            self._flash_msg(f"✔ liberaste {human(freed)}")
        self._refresh_disk()

    def _on_done(self, st):
        self._busy(False)
        self.worker = None
        if st.get("aborted"):
            self.status.set("abortado: no hay lugar en el destino")
        else:
            if not st["cancelled"]:
                self.bar["value"] = 1000
            self._on_progress(st, 1)
            lvl = "err" if st["errors"] else ("warn" if st["cancelled"] or st["skipped"] else "ok")
            self._write(lvl, f"✔ fin · copiados {st['copied']} · ya estaban {st['already']} · borrados {st['deleted']} · "
                             f"LIBERADO {human(st['freed'])} · salteados {st['skipped']} · errores {st['errors']}")
            if st.get("log_path"):
                self._write("info", f"log: {st['log_path']}")
            self.status.set("listo." if not st["cancelled"] else "cancelado.")
            self.root.bell()
        if self._saved_sources is not None:  # vuelve la lista manual que había antes de GUARDAR
            self.sources, self._saved_sources = [s for s in self._saved_sources if os.path.exists(s)], None
        elif self.mode.get() == MODE_MOVE:
            self.sources = [s for s in self.sources if os.path.exists(s)]
        self._refresh_list()
        if st.get("freed"):
            self._forget_gone()
            self._after_change(st["freed"])

    def _on_an_done(self, rep):
        self._scan_phase = None
        self._busy(False)
        self.worker = None
        self._stop_bar(rep is not None)
        if rep is None:
            self.status.set("análisis cancelado.")
            self._render_cards()
            self._update_totals(animate=False)
            return
        self.hero["clean"] = self.hero["keep"] = 0.0  # los números grandes cuentan desde cero
        self.report = rep
        self._render_report()
        self._refresh_disk()
        self.status.set(f"análisis listo en {rep.elapsed:.1f}s · {rep.n_dirs:,} carpetas".replace(",", "."))
        self._write("ok", "✔ " + self.an_summary.get())
        if rep.errors:
            self._write("info", f"{rep.errors} carpetas/archivos sin permiso de lectura (normal en carpetas de sistema).")
        self.root.bell()

    def _on_del_done(self, res):
        self._busy(False)
        self.worker = None
        self._stop_bar()
        gone = set(res["deleted"])
        lvl = "warn" if res["errors"] else "ok"
        self._write(lvl, f"✔ borrado · {len(gone)} elemento(s) · LIBERADO {human(res['freed'])}"
                         + (" · papelera vaciada" if res.get("bin") else "")
                         + (f" · {res['errors']} con problemas" if res["errors"] else ""))
        if res.get("log_path"):
            self._write("info", f"log: {res['log_path']}")
        self.stats_txt.set(f"liberado en esta pasada: {human(res['freed'])}")
        self.status.set("listo.")
        if self.report and res.get("bin"):
            self.report.recs = [r for r in self.report.recs if r.key != "bin"]
        self._forget_gone(gone)
        self._after_change(res["freed"])


def main():
    sys.setrecursionlimit(10000)
    migrated = migrate_legacy()
    if "--preset" in sys.argv[1:]:  # modo programado: sin ventana
        sys.exit(auto_main(sys.argv[1:]))
    if tk is None:
        print("Falta tkinter. Viene con el instalador oficial de python.org (en Mac con Homebrew: brew install python-tk).")
        sys.exit(1)
    try:  # nitidez en pantallas HiDPI de Windows (antes de crear la ventana)
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass
    try:  # que la barra de tareas muestre nuestro icono y no el de Python
        from ctypes import windll
        windll.shell32.SetCurrentProcessExplicitAppUserModelID("Teskadev.LimpiaGuarda")
    except Exception:  # noqa: BLE001
        pass
    dnd_files = None
    try:
        from tkinterdnd2 import TkinterDnD, DND_FILES
        root = TkinterDnD.Tk()
        dnd_files = DND_FILES
    except Exception:  # noqa: BLE001
        root = tk.Tk()
    if IS_MAC:  # macOS dibuja las fuentes a 72 ppp: se agrandan para que se vean como en Windows
        root.tk.call("tk", "scaling", 1.333)
    app = App(root, dnd_files)
    for m in migrated:
        app._write("info", f"de Teska Vault a {APP_NAME}: {m}")
    root.mainloop()


if __name__ == "__main__":
    main()
