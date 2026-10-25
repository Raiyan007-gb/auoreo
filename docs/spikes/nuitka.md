# Spike: Nuitka onefile (spec L1095, P2: cold start, size, AV scan)

**Decision:** **PyInstaller stays the release builder.** A Nuitka onefile build of the full CLI
did **not finish within the time box** in this container. It hit two optimizer crashes in
litellm (one is worked around in the repo) and needed a C compilation of **7,175 modules**,
projected at more than an hour per OS. `scripts/build_binary_nuitka.py` is kept as an
**experimental** builder with the same inputs as the PyInstaller spec, so the attempt can be
repeated on a bigger machine or with a slimmer import graph.

## What was built

`scripts/build_binary_nuitka.py` mirrors `packaging/pyinstaller/auoreo.spec`, using the same
`collect.py` inputs:

- **Definition folders and `web/dist`** are placed under `auoreo/_bundled/` in the bundle. A
  Nuitka binary has no `sys._MEIPASS`, and `auoreo.core.paths.bundled_root()` already falls
  back to `<package>/_bundled`, so no engine change was needed.
  - `--include-data-dir` skips code files, but tool handlers and backend impls are `.py` files
    loaded by path. They are added again with `--include-data-files=<dir>=<dst>/=**/*.py`.
    Two attempts failed before this form was right: a missing trailing slash ("target has no
    trailing slash"), and `**/*` matching directories ("cannot include directory … as a data
    file").
- **Alembic scripts and `auoreo_plugin_rt.py`** are included as `.py` data files.
- **Native and package data:** `sqlite_vec/vec0.so` next to its package; litellm and tiktoken
  package data.
- **Modules:** `--include-package` for auoreo, uvicorn and litellm. The definition imports from
  `collect.definition_imports()` use `--include-module`, matching PyInstaller's top-level
  hidden imports. `--nofollow-import-to` covers `collect.EXCLUDED_MODULES`, `litellm.proxy`
  and `litellm.tests`.
- **Workaround file:** `scripts/spikes/auoreo.nuitka-package.config.yml`, a Nuitka user package
  config. Its anti-bloat `replacements_plain` rewrites one litellm `match` statement (see
  failure 3 below).
- `scripts/spikes/coldstart.py` measures size, `--version` and `doctor` wall time for any set
  of onefile binaries. Each run gets a fresh `AUOREO_HOME`.

## Attempts (this container: 4 cores, 15 GB RAM, Python 3.13.17 Debian build, gcc 13)

| # | Nuitka | Result |
|---|---|---|
| 1 | 4.3.1 | `FATAL: … matches more than one file, but target has no trailing slash` (data-file syntax) |
| 2 | 4.3.1 | `FATAL: standalone mode on Linux requires 'patchelf'`; fixed with `uv run --with patchelf` (PyPI wheel) |
| 3 | 4.3.1 | `FATAL: cannot include directory … as a data file` (`**/*` matches directories); fixed with data-dir + `**/*.py` |
| 4 | 4.3.1 | **Optimizer crash** (`AssertionError`, crash report) at `litellm/litellm_core_utils/streaming_chunk_builder_utils.py:355`: `case StreamingChoices(delta=Delta(role=str() as role)) \| {"delta": {"role": str() as role}} if role:`, an or-pattern whose alternatives bind the same capture, with a guard (litellm 1.106.3) |
| 5 | 2.8.10 (`nuitka<4`) | **same crash**, same line: not a 4.x regression |
| 6 | 4.3.1 + package config splitting that `case` into two | Python-level compilation ✅ (about 9 min), C code generation ✅ (about 10 min), then SCons/gcc on **7,175 C files** (3.5 GB build folder). Killed after 25 min total with 636/7,175 objects compiled, at about 105 objects/min with `--jobs 3`. Projected about 60 more minutes before linking and onefile compression |

The machine was shared with a `cargo build`, a PyInstaller build and the test suite during
attempt 6, so per-object speed is pessimistic. The order of magnitude still holds: about 7k
modules must be compiled to C. Most of them are litellm (provider modules pulled in by
`--include-package=litellm`, which the lazy imports require) plus the transitive graph.

## PyInstaller re-measured (same container)

`uv run --with 'pyinstaller>=6.22,<7' python scripts/build_binary.py --dist dist/pyinstaller`:
**150 s** build, onefile **203.9 MB** (sha256 `4d33ce69…`).

| `scripts/spikes/coldstart.py --runs 5` | first | median | min | max |
|---|---|---|---|---|
| `auoreo --version` | 2.24 s | 2.59 s | 2.24 s | 3.87 s |
| `auoreo doctor` (fresh home) | 6.58 s | 6.95 s | 6.58 s | 8.04 s |

The machine was quiet for this run (load average 1.1). For reference, the brief's earlier
figures were ~203 MB, `--version` ~2.3 s and `doctor` ~3.5 s. Size and `--version` match.
`doctor` now takes about 7 s, probably because it has gained rows since those figures were
taken (RTC, ICE, plugins). The cause was not investigated in this spike. Nuitka size and cold start: **not measured** (no binary).

## AV scan

AV scanning cannot run here (no Windows Defender or VirusTotal access from the container, and
no signed Windows build). Onefile self-extracting binaries from both PyInstaller and Nuitka are
known to draw heuristic false positives. Both projects document that code signing is the main
mitigation. Measure this on the signed Windows release artefacts (docs/release.md) as a user
checkpoint: upload `auoreo-windows-x64.exe` from both builders to VirusTotal and compare
detection counts.

## Options

| | PyInstaller (current) | Nuitka onefile |
|---|---|---|
| Build time (linux-x64, here) | 150 s | > 25 min (time box), projected ≈ 80–90 min |
| Extra toolchain | none | C compiler per OS (MSVC/clang/gcc), patchelf on Linux, ≈ 4 GB scratch disk |
| Compatibility | works on the full graph (the release matrix is green) | optimizer crashes on newer syntax in deps; each litellm upgrade can break the build again (attempts 4–5) |
| Startup | bytecode unpack + import | compiled C: potentially faster imports. Unmeasured; onefile still unpacks |
| Size | 203.9 MB | unmeasured |

## Decision

Keep PyInstaller. Revisit Nuitka only together with a **slimmer import graph**: lazy-load
litellm providers through `--nofollow-import-to` lists, or stop bundling litellm in the
binary in favour of a wheel-installed gateway. At 7k modules it is too slow, and too fragile
across dependency upgrades, for a four-OS CI matrix. Cold-start gains, if any, cannot justify
that before they are measured.

## Follow-ups

1. Run `scripts/build_binary_nuitka.py --jobs 16` on a large CI runner once. Then measure size
   and cold start with `scripts/spikes/coldstart.py dist/pyinstaller/auoreo dist/nuitka/auoreo`.
2. Report the or-pattern + guard crash upstream to Nuitka, with the crash report and the
   one-line reproducer above.
3. AV comparison on signed Windows builds (user checkpoint).
