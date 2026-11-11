import ctypes
import importlib.util
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

_DLL_DIR_HANDLES: list[object] = []
_LOADED_DLLS: list[object] = []
_PREPARED = False

CRT_DLL_NAMES = (
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "concrt140.dll",
)

QT_PRELOAD_DLL_NAMES = (
    "Qt6Core.dll",
    "Qt6Gui.dll",
    "Qt6Widgets.dll",
    "Qt6OpenGL.dll",
    "Qt6OpenGLWidgets.dll",
    "Qt6Charts.dll",
)

MODERN_CRT_SYMBOL = b"_Thrd_sleep_for"


def _find_package_dir(package_name: str) -> Path | None:
    try:
        spec = importlib.util.find_spec(package_name)
    except (ImportError, ValueError):
        return None
    if spec and spec.submodule_search_locations:
        for location in spec.submodule_search_locations:
            path = Path(location)
            if path.is_dir():
                return path
    return None


def _is_conflicting_qt_dir(directory: Path, allowed_roots: tuple[Path, ...]) -> bool:
    try:
        resolved = directory.resolve()
    except OSError:
        return False
    for root in allowed_roots:
        if resolved == root or root in resolved.parents:
            return False
    for marker in ("Qt6Core.dll", "Qt5Core.dll", "shiboken6.abi3.dll", "pyside6.abi3.dll"):
        if (directory / marker).is_file():
            return True
    return False


def _sanitize_path_env(priority_dirs: list[Path], allowed_roots: tuple[Path, ...]) -> None:
    raw_path = os.environ.get("PATH", "")
    existing_parts = [p for p in raw_path.split(os.pathsep) if p]
    filtered_parts: list[str] = []
    seen: set[str] = set()

    for d in priority_dirs:
        if d.is_dir():
            norm = os.path.normcase(str(d.resolve()))
            if norm not in seen:
                seen.add(norm)
                filtered_parts.append(str(d))

    for part in existing_parts:
        path_obj = Path(part)
        if _is_conflicting_qt_dir(path_obj, allowed_roots):
            continue
        try:
            norm = os.path.normcase(str(path_obj.resolve()))
        except OSError:
            norm = os.path.normcase(part)
        if norm not in seen:
            seen.add(norm)
            filtered_parts.append(part)

    os.environ["PATH"] = os.pathsep.join(filtered_parts)


def _candidate_crt_dirs(pyside_dir: Path | None, shiboken_dir: Path | None) -> list[Path]:
    candidates: list[Path] = []
    prefix = Path(sys.prefix)
    base_prefix = Path(sys.base_prefix)

    for d in (
        pyside_dir,
        shiboken_dir,
        prefix / "Scripts",
        prefix / "Lib" / "site-packages" / "msvc_runtime",
        prefix / "Library" / "bin",
        prefix,
        base_prefix / "Scripts",
        base_prefix / "DLLs",
        base_prefix / "Library" / "bin",
        base_prefix,
    ):
        if d and d.is_dir():
            candidates.append(d)

    program_files = [
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")),
        Path(os.environ.get("ProgramFiles", "C:/Program Files")),
        Path(os.environ.get("LOCALAPPDATA", "")),
    ]
    glob_patterns = (
        "Microsoft/EdgeWebView/Application/*",
        "Microsoft/Edge/Application/*",
        "Programs/PyCharm*/jbr/bin",
        "Programs/PyCharm*/bin",
        "JetBrains/PyCharm*/jbr/bin",
        "JetBrains/PyCharm*/bin",
        "JetBrains/Toolbox/apps/PyCharm*/*/*/jbr/bin",
        "Microsoft Visual Studio/*/*/VC/Redist/MSVC/*/x64/Microsoft.VC143.CRT",
    )
    for root in program_files:
        if not root or not root.is_dir():
            continue
        for pattern in glob_patterns:
            matched = sorted(root.glob(pattern), reverse=True)
            for item in matched:
                if item.is_dir() and (item / "msvcp140.dll").is_file():
                    candidates.append(item)

    sys_root = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"
    if sys_root.is_dir():
        candidates.append(sys_root)

    return candidates


def _exports_modern_msvcp(directory: Path) -> bool:
    """Проверяет наличие символа MSVC 14.40+ без загрузки устаревшей DLL в процесс."""
    msvcp = directory / "msvcp140.dll"
    if not msvcp.is_file():
        return False
    try:
        return MODERN_CRT_SYMBOL in msvcp.read_bytes()
    except OSError:
        return False


def _qt_requires_modern_msvcp(pyside_dir: Path | None) -> bool:
    if pyside_dir is None:
        return False
    qt_core = pyside_dir / "Qt6Core.dll"
    if not qt_core.is_file():
        return False
    try:
        return MODERN_CRT_SYMBOL in qt_core.read_bytes()
    except OSError:
        return False


def _sync_crt_into_pyside(source_dir: Path, target_dirs: list[Path]) -> None:
    for target_dir in target_dirs:
        if not target_dir.is_dir() or source_dir.resolve() == target_dir.resolve():
            continue
        if _exports_modern_msvcp(target_dir):
            continue
        for dll_name in CRT_DLL_NAMES:
            src_file = source_dir / dll_name
            dst_file = target_dir / dll_name
            if src_file.is_file():
                try:
                    shutil.copy2(src_file, dst_file)
                except OSError:
                    pass


def _add_dll_dir(directory: Path) -> None:
    add_fn = getattr(os, "add_dll_directory", None)
    if add_fn is None or not directory.is_dir():
        return
    try:
        handle = add_fn(str(directory.resolve()))
        _DLL_DIR_HANDLES.append(handle)
    except OSError:
        pass


def _preload_dll(dll_path: Path) -> bool:
    if not dll_path.is_file():
        return False
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        return False
    try:
        _LOADED_DLLS.append(win_dll(str(dll_path.resolve())))
        return True
    except OSError:
        return False


def _install_msvc_runtime() -> bool:
    if sys.platform != "win32":
        return False
    commands = [
        ["uv", "pip", "install", "--python", sys.executable, "msvc-runtime>=14.40"],
        [sys.executable, "-m", "pip", "install", "--quiet", "msvc-runtime>=14.40"],
        ["uv", "pip", "install", "--python", sys.executable, "PySide6>=6.7,<6.8"],
        [sys.executable, "-m", "pip", "install", "--quiet", "PySide6>=6.7,<6.8"],
    ]
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            if result.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def prepare_qt_environment(*, force: bool = False) -> None:
    """Настраивает пути поиска DLL для PySide6 в Windows до импорта QtCore."""
    global _PREPARED
    if _PREPARED and not force:
        return
    _PREPARED = True

    if sys.platform != "win32":
        return

    shiboken_dir = _find_package_dir("shiboken6")
    pyside_dir = _find_package_dir("PySide6")
    prefix = Path(sys.prefix)
    base_prefix = Path(sys.base_prefix)

    allowed_roots = tuple(p.resolve() for p in (prefix, base_prefix) if p.exists())
    priority_dirs = [
        d
        for d in (
            pyside_dir,
            shiboken_dir,
            prefix / "Scripts",
            prefix,
            base_prefix / "Scripts",
            base_prefix / "DLLs",
            base_prefix,
        )
        if d is not None and d.is_dir()
    ]

    _sanitize_path_env(priority_dirs, allowed_roots)

    for d in priority_dirs:
        _add_dll_dir(d)

    if pyside_dir:
        plugins = pyside_dir / "plugins"
        if plugins.is_dir():
            os.environ["QT_PLUGIN_PATH"] = str(plugins)
        os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)

    crt_dirs = _candidate_crt_dirs(pyside_dir, shiboken_dir)
    selected_crt: Path | None = None
    for candidate in crt_dirs:
        if _exports_modern_msvcp(candidate):
            selected_crt = candidate
            break

    if (
        selected_crt is None
        and _qt_requires_modern_msvcp(pyside_dir)
        and os.environ.get("ENERGY_HOME_CRT_CHECKED") != "1"
    ):
        os.environ["ENERGY_HOME_CRT_CHECKED"] = "1"
        if _install_msvc_runtime():
            shiboken_dir = _find_package_dir("shiboken6")
            pyside_dir = _find_package_dir("PySide6")
            for candidate in _candidate_crt_dirs(pyside_dir, shiboken_dir):
                if _exports_modern_msvcp(candidate):
                    selected_crt = candidate
                    break

    if selected_crt is None and crt_dirs:
        selected_crt = crt_dirs[0]

    if selected_crt:
        _add_dll_dir(selected_crt)
        targets = [d for d in (pyside_dir, shiboken_dir) if d is not None]
        _sync_crt_into_pyside(selected_crt, targets)
        for dll_name in CRT_DLL_NAMES:
            _preload_dll(selected_crt / dll_name)

    if shiboken_dir:
        for dll_file in sorted(shiboken_dir.glob("shiboken6*.dll")):
            _preload_dll(dll_file)

    if pyside_dir:
        for dll_name in CRT_DLL_NAMES:
            _preload_dll(pyside_dir / dll_name)
        for dll_file in sorted(pyside_dir.glob("pyside6*.dll")):
            _preload_dll(dll_file)
        for qt_dll in QT_PRELOAD_DLL_NAMES:
            _preload_dll(pyside_dir / qt_dll)


def ensure_pyside6() -> tuple[bool, str | None]:
    """Проверяет загрузку PySide6 и при необходимости восстанавливает DLL-окружение."""
    prepare_qt_environment()
    try:
        from PySide6.QtCore import QLocale  # noqa: F401
        from PySide6.QtWidgets import QApplication  # noqa: F401

        return True, None
    except ImportError as first_exc:
        logging.warning("Не удалось загрузить PySide6 с первой попытки: %s", first_exc)
        if sys.platform == "win32" and os.environ.get("ENERGY_HOME_QT_REPAIRED") != "1":
            os.environ["ENERGY_HOME_QT_REPAIRED"] = "1"
            if _install_msvc_runtime():
                prepare_qt_environment(force=True)
                try:
                    from PySide6.QtCore import QLocale  # noqa: F401
                    from PySide6.QtWidgets import QApplication  # noqa: F401

                    return True, None
                except ImportError:
                    pass
        return False, str(first_exc)
