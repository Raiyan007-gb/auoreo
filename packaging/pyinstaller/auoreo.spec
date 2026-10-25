# -*- mode: python ; coding: utf-8 -*-
# PyInstaller onefile spec for `auoreo` (W3-A). Build with `python scripts/build_binary.py`.
#
# Bundle root (= sys._MEIPASS, which `auoreo.core.paths.bundled_root()` returns when frozen):
#   agents/ tools/ skills/ ... memory/   bundled definitions (BUNDLED_DIRS)
#   web/dist/                            the SPA served at /
#   auoreo/core/alembic/**.py            migration scripts (Alembic loads them by path)
#   sqlite_vec/vec0.*                    sqlite-vec loadable extension
import importlib.util
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

HERE = Path(SPECPATH).resolve()  # noqa: F821 (injected by PyInstaller)
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from collect import EXCLUDED_MODULES, data_dirs, definition_imports  # noqa: E402

datas = [
    *data_dirs(ROOT),
    *collect_data_files("auoreo", include_py_files=True, subdir="core/alembic"),
    *collect_data_files("auoreo", include_py_files=True, subdir="plugins/rt"),
    *collect_data_files("auoreo"),
    *collect_data_files("litellm"),
    *collect_data_files("tiktoken_ext", include_py_files=True),
]
# sqlite_vec.load() opens `<package dir>/vec0.<so|dylib|dll>`; collect_dynamic_libs() misses
# it (no lib* prefix), so name it explicitly and keep it under sqlite_vec/.
_vec_dir = Path(importlib.util.find_spec("sqlite_vec").origin).parent
binaries = [
    *collect_dynamic_libs("sqlite_vec"),
    *((str(p), "sqlite_vec") for p in _vec_dir.glob("vec0.*") if p.suffix in {".so", ".dylib", ".dll"}),
]
hiddenimports = [
    *collect_submodules("auoreo"),
    *collect_submodules("uvicorn"),
    *collect_submodules("litellm", filter=lambda n: not n.startswith(("litellm.proxy", "litellm.tests"))),
    "tiktoken_ext.openai_public",
    *definition_imports(ROOT),
]

a = Analysis(  # noqa: F821
    [str(HERE / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=list(EXCLUDED_MODULES),
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="auoreo",
    console=True,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    codesign_identity=None,  # signed afterwards in CI (docs/release.md), never with a local identity
    entitlements_file=None,
)
