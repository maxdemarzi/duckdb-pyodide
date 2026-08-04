# duckdb-pyodide

Builds a `duckdb` wheel for [Pyodide](https://pyodide.org/) (Python compiled to
WebAssembly, for running in a browser), pinned to an **exact** DuckDB core version --
rather than whichever version Pyodide's own bundled `duckdb` package happens to track.

## Why this exists

DuckDB's extension binary format is version-locked: a compiled extension built against
one DuckDB core commit will not load into a different one, even a nearby patch version.
If you ship your own compiled DuckDB extension and want it to load inside Pyodide, you
need a `duckdb` Pyodide wheel built from the *same* core commit your extension targets --
which generally will not be whatever version Pyodide bundles by default.

This came out of getting a real, third-party DuckDB extension loading and running
queries inside Pyodide end to end. [duckdb/duckdb-pyodide#2](https://github.com/duckdb/duckdb-pyodide/issues/2)
has a DuckDB maintainer confirming extension loading isn't supported out of the box in
the official Pyodide build -- getting it working needed (1) a `duckdb` wheel at the
exact right version, tagged with a matching platform string, and (2) a runtime fix for a
Pyodide symbol-visibility default that blocks the extension's internal `dlopen()` call.
This repo covers (1). [xlwings/duckdb-pyodide](https://github.com/xlwings/duckdb-pyodide)
explored the same territory earlier and was a useful reference for the general shape of
the problem; this repo is an independent implementation (that project has no LICENSE
file, so nothing here is copied from it), narrower in scope (one pinned version at a
time, driven by explicit inputs, rather than a version matrix).

## Usage

Via GitHub Actions (`workflow_dispatch` on `build.yml`), or locally:

```
pip install pyodide-build
# install a matching emsdk first -- see `pyodide config get emscripten_version`
python scripts/build.py \
  --duckdb-version v1.5.4 \
  --xbuildenv-url https://github.com/pyodide/pyodide-build-environment-nightly/releases/download/<date>/xbuildenv-<date>.tar.bz2 \
  --platform-tag wasm_eh_pyodide
```

Produces a wheel in `dist/`. `--platform-tag` is baked into the compiled binary itself
(what `PRAGMA platform` returns at runtime) -- it needs to match whatever platform tag
your own DuckDB extension was built with, or extension loading will fail even with a
version match.

**Note on the Pyodide cross-build environment**: the *stable* `pyodide xbuildenv search`
index lags well behind Pyodide's actual releases (as of this writing, stuck on Python
3.12.7). Current nightlies -- including ones targeting newer CPython versions -- are
published separately at
[pyodide/pyodide-build-environment-nightly](https://github.com/pyodide/pyodide-build-environment-nightly/releases).

## What gets patched, and why

`scripts/patch_cmake.py` -- duckdb-python's own `CMakeLists.txt` links two GNU-ld-specific
flags (`--export-dynamic-symbol`) that Emscripten's linker (`wasm-ld`) doesn't implement.
Symbol export for a Pyodide build is instead handled by `pyodide build
--exports=whole_archive` at the packaging layer, so the fix is to skip that flag pair
under Emscripten, not to find a wasm-ld equivalent. As of duckdb-python v1.5.4, upstream
restructured this part of the build (`duckdb_loader.cmake`) and no longer emits the
GNU-ld-only block at all -- the script detects this and no-ops rather than failing.

## License

MIT, see [LICENSE](LICENSE).
