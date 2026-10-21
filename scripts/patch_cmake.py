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

# duckdb-python links nanobind-static into `_duckdb` twice: once implicitly via
# nanobind_add_module(), and again through the _duckdb_dependencies INTERFACE
# target. A native link tolerates that -- the archive appears twice but each
# member is pulled in once. `pyodide build --exports=whole_archive` force-
# includes every archive on the link line, so the second mention duplicates
# every nanobind symbol and wasm-ld fails outright:
#
#   wasm-ld: error: duplicate symbol:
#       nanobind::detail::internals_inc_ref(nanobind::detail::nb_internals*)
#
# Dropping nanobind-static from the INTERFACE link (Emscripten only) leaves
# nanobind_add_module as its single linker mention. The object libraries that
# consume _duckdb_dependencies only need its headers and compile definitions,
# which are propagated explicitly instead -- see the comment above
# nanobind_build_library in duckdb-python's own CMakeLists.txt.
NANOBIND_DOUBLE_LINK = (
    "target_link_libraries(_duckdb_dependencies INTERFACE nanobind-static\n"
    "                                                     duckdb_target)"
)

# duckdb-python v2.0.0 stopped naming the library literally: it picks
# `nanobind-static-abi3` or `nanobind-static` into DUCKDB_PY_NANOBIND (the
# stable-ABI build that the 2.0.0 wheels on PyPI use) and links the VARIABLE.
# Same double link, same whole-archive failure, so the patch is the same shape
# with the variable kept intact -- which also means it keeps working whichever
# branch the abi3 condition takes under Pyodide. Validated against the branch
# tip on 2026-10-13, where the literal form was still in place; the v2.0.0 tag
# is where it changed, and this script REFUSED rather than guessing, which is
# how the change was noticed at all.
NANOBIND_DOUBLE_LINK_ABI3 = (
    "target_link_libraries(_duckdb_dependencies INTERFACE ${DUCKDB_PY_NANOBIND}\n"
    "                                                     duckdb_target)"
)

NANOBIND_SINGLE_LINK_ABI3 = """if(EMSCRIPTEN)
  # patched by duckdb-pyodide: link the nanobind library once
  # (nanobind_add_module already does), but keep propagating its
  # headers/definitions. DUCKDB_PY_NANOBIND is whichever of
  # nanobind-static-abi3 / nanobind-static this configure selected.
  target_link_libraries(_duckdb_dependencies INTERFACE duckdb_target)
  target_include_directories(
    _duckdb_dependencies
    INTERFACE $<TARGET_PROPERTY:${DUCKDB_PY_NANOBIND},INTERFACE_INCLUDE_DIRECTORIES>)
  target_compile_definitions(
    _duckdb_dependencies
    INTERFACE $<TARGET_PROPERTY:${DUCKDB_PY_NANOBIND},INTERFACE_COMPILE_DEFINITIONS>)
else()
  target_link_libraries(_duckdb_dependencies INTERFACE ${DUCKDB_PY_NANOBIND}
                                                       duckdb_target)
endif()"""

NANOBIND_SINGLE_LINK = """if(EMSCRIPTEN)
  # patched by duckdb-pyodide: link nanobind-static once (nanobind_add_module
  # already does), but keep propagating its headers/definitions.
  target_link_libraries(_duckdb_dependencies INTERFACE duckdb_target)
  target_include_directories(
    _duckdb_dependencies
    INTERFACE $<TARGET_PROPERTY:nanobind-static,INTERFACE_INCLUDE_DIRECTORIES>)
  target_compile_definitions(
    _duckdb_dependencies
    INTERFACE $<TARGET_PROPERTY:nanobind-static,INTERFACE_COMPILE_DEFINITIONS>)
else()
  target_link_libraries(_duckdb_dependencies INTERFACE nanobind-static
                                                       duckdb_target)
endif()"""


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


def patch_gnu_ld_exports(cmake_path: pathlib.Path) -> None:
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


def patch_nanobind_double_link(cmake_path: pathlib.Path) -> None:
    original = cmake_path.read_text()
    if "patched by duckdb-pyodide" in original:
        print(f"{cmake_path}: nanobind link already patched -- nothing to do.")
        return
    for double, single, what in (
        (NANOBIND_DOUBLE_LINK, NANOBIND_SINGLE_LINK, "nanobind-static"),
        (NANOBIND_DOUBLE_LINK_ABI3, NANOBIND_SINGLE_LINK_ABI3, "${DUCKDB_PY_NANOBIND}"),
    ):
        if double in original:
            cmake_path.write_text(original.replace(double, single, 1))
            print(f"Patched {cmake_path}: link {what} once under Emscripten "
                  "(nanobind_add_module already links it; --exports=whole_archive turns "
                  "the second mention into duplicate-symbol link errors).")
            return
    if "nanobind-static" not in original:
        print(f"{cmake_path}: no nanobind-static link found -- nothing to patch.")
        return
    print(f"ERROR: {cmake_path} links nanobind-static in a shape this script "
          "doesn't recognize -- refusing to guess, because leaving it unpatched "
          "fails at link time with duplicate nanobind symbols. Relevant lines:")
    for lineno, line in enumerate(original.splitlines(), start=1):
        if "nanobind" in line:
            print(f"  {lineno}: {line}")
    sys.exit(1)


if __name__ == "__main__":
    patch_gnu_ld_exports(pathlib.Path("CMakeLists.txt"))
    patch_nanobind_double_link(pathlib.Path("CMakeLists.txt"))
