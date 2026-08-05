#!/usr/bin/env python3
"""Check that an installed Pyodide cross-build environment actually matches the
Pyodide runtime the resulting wheel is meant to load into.

The build takes the cross-build environment and the target Pyodide version as
two separate inputs, and nothing forces them to agree -- the xbuildenv URL
cannot be derived from the Pyodide version, because the nightly xbuildenvs this
repo relies on are keyed by build date rather than by Pyodide release. A
mismatched pair still builds a perfectly good wheel; it just produces one that
the target runtime refuses to install, which previously only surfaced at the
smoke test after a full DuckDB compile.

This compares three values that must agree, and fails in seconds instead:

    CPython version   -- a cp313 extension module will not import on a 3.14 runtime
    Emscripten version -- decides the wheel's `emscripten_x_y_z_wasm32` tag
    Pyodide ABI version -- decides the `pyodide_<abi>_wasm32` tag

The xbuildenv side is read from `pyodide config get`, which reflects whichever
environment is currently installed. The runtime side is read from the target
release's own `pyodide-lock.json`, fetched from the npm CDN -- the same file
Pyodide itself ships, so it is authoritative rather than a hand-maintained table.

On success the Emscripten version is printed to stdout, so the caller can
install a matching emsdk rather than guessing one from pyodide-build's default.

Usage:
    python scripts/check_xbuildenv.py --pyodide-npm-version 0.28.3
"""
import argparse
import json
import subprocess
import sys
import urllib.request

LOCK_URL = "https://cdn.jsdelivr.net/npm/pyodide@{version}/pyodide-lock.json"


def pyodide_config(key: str) -> str:
    result = subprocess.run(
        ["pyodide", "config", "get", key],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def target_info(pyodide_npm_version: str) -> dict:
    url = LOCK_URL.format(version=pyodide_npm_version)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return json.load(response)["info"]
    except Exception as exc:
        sys.exit(f"ERROR: could not read {url}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyodide-npm-version", required=True, help="target pyodide npm version, e.g. 0.28.3")
    args = parser.parse_args()

    info = target_info(args.pyodide_npm_version)

    # `platform` is e.g. "emscripten_4_0_9"; the trailing three parts are the version.
    runtime_emscripten = info["platform"].removeprefix("emscripten_").replace("_", ".")
    xbuildenv_emscripten = pyodide_config("emscripten_version")

    # setup-python resolves a patch version of its own, so compare on major.minor.
    runtime_python = ".".join(info["python"].split(".")[:2])
    xbuildenv_python = ".".join(pyodide_config("python_version").split(".")[:2])

    runtime_abi = info["abi_version"]
    xbuildenv_abi = pyodide_config("pyodide_abi_version")

    checks = [
        ("CPython", xbuildenv_python, runtime_python),
        ("Emscripten", xbuildenv_emscripten, runtime_emscripten),
        ("Pyodide ABI", xbuildenv_abi, runtime_abi),
    ]

    mismatches = [(name, got, want) for name, got, want in checks if got != want]
    # Report to stderr so stdout carries only the Emscripten version, which the
    # caller captures to pin emsdk.
    for name, got, want in checks:
        status = "MISMATCH" if (name, got, want) in mismatches else "ok"
        print(f"  {name:<12} xbuildenv={got:<8} runtime={want:<8} [{status}]", file=sys.stderr)

    if mismatches:
        names = ", ".join(name for name, _, _ in mismatches)
        sys.exit(
            f"\nERROR: cross-build environment does not match pyodide "
            f"{args.pyodide_npm_version} ({names}).\n"
            f"The wheel would build but the target runtime would refuse to install it.\n"
            f"Pass an --xbuildenv-url built for this Pyodide release, or target the "
            f"Pyodide version this environment was built for."
        )

    print(xbuildenv_emscripten)


if __name__ == "__main__":
    main()
