#!/usr/bin/env python3
"""Adjust duckdb-python's CMakeLists.txt for an Emscripten (wasm32) target.

Why this is needed at all: on a real UNIX/Linux/macOS build, duckdb-python's
own CMakeLists.txt links `_duckdb` with two GNU-ld-specific flags,
`LINKER:--export-dynamic-symbol=duckdb_adbc_init` and
`LINKER:--export-dynamic-symbol=PyInit__duckdb`, to force those two symbols
into the shared object's dynamic symbol table. Emscripten's linker (wasm-ld)
does not implement `--export-dynamic-symbol` at all -- passing it is a hard
link error under `emcmake`. Symbol export for a Pyodide build is instead
handled entirely by `pyodide build --exports=whole_archive` at the Python
packaging layer, so the fix is simply to skip that flag pair when the target
is Emscripten, not to find an equivalent wasm-ld flag.

As of duckdb-python v1.5.4, upstream restructured CMakeLists.txt to build
`_duckdb` via `duckdb_loader.cmake`/`duckdb_add_library`/`duckdb_link_extensions`
and no longer emits this GNU-ld-specific block at all -- there is nothing left
to patch for that version. This script accounts for both shapes: a real
in-place edit against the old block, or a confirmed no-op against the new one.
Anything else (the file exists but matches neither known shape) is a loud
failure, not a silent skip -- a future duckdb-python restructuring could
introduce a *different* GNU-ld-only flag this same wasm-ld incompatibility
would apply to, and that needs a human to look, not an unnoticed pass-through.
"""
import pathlib
import sys

GNU_LD_EXPORT_BLOCK = (
    "elseif(UNIX AND NOT APPLE)\n"
    "  target_link_options(\n"
    '    _duckdb PRIVATE "LINKER:--export-dynamic-symbol=duckdb_adbc_init"\n'
    '    "LINKER:--export-dynamic-symbol=PyInit__duckdb")'
)

EMSCRIPTEN_GUARD = (
    "elseif(EMSCRIPTEN)\n"
    "  # wasm-ld has no --export-dynamic-symbol equivalent; symbol export for\n"
    "  # a Pyodide build is handled by `pyodide build --exports=whole_archive`\n"
    "  # instead, at the Python packaging layer, not at link time.\n"
) + GNU_LD_EXPORT_BLOCK


def patch(cmake_path: pathlib.Path) -> None:
    original = cmake_path.read_text()

    if GNU_LD_EXPORT_BLOCK in original:
        cmake_path.write_text(original.replace(GNU_LD_EXPORT_BLOCK, EMSCRIPTEN_GUARD, 1))
        print(f"Patched {cmake_path}: inserted an EMSCRIPTEN guard ahead of the "
              "GNU-ld-only --export-dynamic-symbol block.")
        return

    if "--export-dynamic-symbol" not in original:
        print(f"{cmake_path}: no --export-dynamic-symbol flags present (this "
              "duckdb-python version builds _duckdb via duckdb_loader.cmake "
              "instead) -- nothing to patch.")
        return

    print(f"ERROR: {cmake_path} references --export-dynamic-symbol in a shape "
          "this script doesn't recognize -- refusing to guess. Relevant lines:")
    for lineno, line in enumerate(original.splitlines(), start=1):
        if "export-dynamic-symbol" in line or "UNIX AND NOT APPLE" in line:
            print(f"  {lineno}: {line}")
    sys.exit(1)


if __name__ == "__main__":
    patch(pathlib.Path("CMakeLists.txt"))
