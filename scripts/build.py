#!/usr/bin/env python3
"""Build a `duckdb` wheel for Pyodide (wasm32-emscripten), pinned to an exact
DuckDB version -- so it can match a downstream consumer's own DuckDB core pin
exactly, rather than whatever version Pyodide happens to bundle.

Why this exists: Pyodide's own bundled `duckdb` package tracks upstream
duckdb-python releases on its own schedule, which will not generally match a
specific downstream project's pinned DuckDB core version. DuckDB's extension
binary format is version-locked -- a native extension built against one
DuckDB core commit will not load into a different one, even a nearby patch
version -- so a project that ships its own compiled DuckDB extension and
wants it loadable inside Pyodide needs a `duckdb` Pyodide wheel built from
the *exact* same core commit, which this script produces on demand.

Usage:
    python scripts/build.py --duckdb-version v1.5.4 \\
        --xbuildenv-url https://github.com/pyodide/pyodide-build-environment-nightly/releases/download/<date>/xbuildenv-<date>.tar.bz2 \\
        --platform-tag wasm_eh_pyodide

Requires `pyodide-build` already installed (`pip install pyodide-build`) and
a matching `emsdk` already activated on PATH (this script does not manage
the Emscripten toolchain itself -- see the accompanying GitHub Actions
workflow for how CI sets that up via `pyodide config get emscripten_version`
before invoking this script).
"""
import argparse
import os
import pathlib
import shutil
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKOUT_DIR = REPO_ROOT / "build" / "duckdb-python"


def run(cmd, **kwargs):
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, **kwargs)


def checkout_duckdb_python(duckdb_version: str) -> pathlib.Path:
    if CHECKOUT_DIR.exists():
        shutil.rmtree(CHECKOUT_DIR)
    CHECKOUT_DIR.parent.mkdir(parents=True, exist_ok=True)
    run([
        "git", "clone",
        "--recurse-submodules", "--shallow-submodules",
        "https://github.com/duckdb/duckdb-python.git",
        str(CHECKOUT_DIR),
    ])
    run(["git", "-C", str(CHECKOUT_DIR), "checkout", duckdb_version])
    run(["git", "-C", str(CHECKOUT_DIR), "submodule", "update", "--init", "--recursive"])
    return CHECKOUT_DIR


def build(duckdb_version: str, xbuildenv_url: str, platform_tag: str,
          version_override: str = "") -> pathlib.Path:
    checkout = checkout_duckdb_python(duckdb_version)

    run([sys.executable, str(pathlib.Path(__file__).parent / "patch_cmake.py")], cwd=checkout)

    env = os.environ.copy()
    env.update({
        # DUCKDB_CUSTOM_PLATFORM (env) only affects the wheel filename tag.
        # DUCKDB_EXPLICIT_PLATFORM (via CMAKE_ARGS) bakes the platform string
        # into the compiled binary itself -- what `PRAGMA platform` returns
        # at runtime, and what a loading extension's own platform tag is
        # checked against. Both need to agree with the consuming project's
        # own extension platform tag, or extension loading fails even with a
        # matching DuckDB core version.
        "DUCKDB_CUSTOM_PLATFORM": platform_tag,
        "CMAKE_ARGS": f"-DDUCKDB_EXPLICIT_PLATFORM={platform_tag}",
        # -include cstdio: nanobind 3.x's nb_types.h calls fprintf(stderr, ...)
        # in two places (the "Critical nanobind error: sequence builder misuse"
        # path) without including <cstdio>. Every platform where this builds
        # gets `stderr` transitively from some other header; this toolchain's
        # libc++ does not, so the compile fails with
        #
        #   nanobind/nb_types.h:1323:13: error: use of undeclared identifier 'stderr'
        #
        # It is nanobind's bug, not duckdb-python's, and not avoidable by
        # choosing a version: 3.0.0, 3.0.1 and 3.1.0 all have the two fprintf
        # sites and none includes <cstdio>, while duckdb-python asks for
        # `nanobind>=3.0` with no upper bound. Forcing the include in is a
        # declaration-only change -- it cannot alter behaviour -- and is far
        # less fragile than patching a build-time dependency's header in
        # site-packages.
        "CFLAGS": "-fwasm-exceptions -include cstdio",
        # --allow-multiple-definition: DuckDB embeds every third-party object
        # library (zstd, yyjson, fsst, re2, miniz, utf8proc, hyperloglog,
        # fastpforlib, skiplistlib, mbedtls, fmt) INTO libduckdb_static.a via
        # $<TARGET_OBJECTS:...> (duckdb/src/CMakeLists.txt's DUCKDB_LINK_LIBS),
        # while each ALSO remains its own installed archive that lands on the
        # link line. A normal link takes one copy; `pyodide build
        # --exports=whole_archive` force-includes every archive, so each of
        # those objects is pulled in twice and wasm-ld fails:
        #
        #   duplicate symbol: duckdb_zstd::ZSTD_fillHashTable(...)
        #   >>> defined in duckdb/src/libduckdb_static.a(zstd_fast.cpp.o)
        #   >>> defined in duckdb/third_party/zstd/libduckdb_zstd.a(zstd_fast.cpp.o)
        #
        # The two definitions are the SAME object file compiled once, so
        # taking the first is not a choice between rival implementations.
        # Stripping the redundant archives instead would mean patching ~11 of
        # them and re-patching whenever that list changes upstream.
        "LDFLAGS": "-fwasm-exceptions -Wl,--allow-multiple-definition",
        "DEFAULT_CROSS_BUILD_ENV_URL": xbuildenv_url,
        # patch_cmake.py's edit (when it makes one) dirties the working tree,
        # which defeats setuptools_scm's normal tag-based version detection
        # (distance=0 + dirty -> can't determine a clean version). Override
        # it directly with the tag we actually checked out.
        #
        # duckdb-python validates this strictly: "Invalid OVERRIDE_GIT_DESCRIBE
        # ... (expected vX.Y.Z with an optional -postN or pre-release suffix)".
        # A tag satisfies that; a BRANCH ref (used to trial-build a line before
        # it is tagged, e.g. v2.0-cyanoptera) does not, so --version-override
        # supplies a conforming string in that case.
        "OVERRIDE_GIT_DESCRIBE": version_override or duckdb_version,
    })

    run(["pyodide", "build", "--exports=whole_archive"], cwd=checkout, env=env)

    wheels = sorted((checkout / "dist").glob("*.whl"))
    if not wheels:
        sys.exit(f"ERROR: no wheel produced in {checkout / 'dist'}")
    if len(wheels) > 1:
        sys.exit(f"ERROR: expected exactly one wheel, found {len(wheels)}: {wheels}")
    return wheels[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duckdb-version", required=True, help="git tag in duckdb/duckdb-python, e.g. v1.5.4")
    parser.add_argument("--xbuildenv-url", required=True, help="Pyodide cross-build environment tarball URL")
    parser.add_argument("--platform-tag", required=True, help="DuckDB platform tag, e.g. wasm_eh_pyodide")
    parser.add_argument("--version-override", default="",
                        help="Version string to build as. Required when --duckdb-version is a BRANCH "
                             "rather than a tag: duckdb-python rejects a non-conforming "
                             "OVERRIDE_GIT_DESCRIBE outright. Its VERSION_RE "
                             "(duckdb_packaging/_versioning.py) accepts vX.Y.Z optionally followed by "
                             "alpha|beta|preview|pre|rc|a|b|c with an optional number, or -postN. "
                             "'dev' is NOT accepted. It must ALSO agree with the checkout's own "
                             "pyproject.toml release_version, so for a branch this is normally just "
                             "that version (e.g. v2.0.0 on v2.0-cyanoptera), not a pre-release suffix.")
    parser.add_argument("--output-dir", type=pathlib.Path, default=REPO_ROOT / "dist")
    args = parser.parse_args()

    wheel = build(args.duckdb_version, args.xbuildenv_url, args.platform_tag, args.version_override)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    dest = args.output_dir / wheel.name
    shutil.copyfile(wheel, dest)
    print(f"Wheel ready: {dest}")


if __name__ == "__main__":
    main()
