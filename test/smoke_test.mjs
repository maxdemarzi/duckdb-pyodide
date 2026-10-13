// Fresh-environment smoke test for a built duckdb Pyodide wheel: no repo
// checkout, no compiler -- install the wheel exactly the way a real
// consumer would (micropip, from a local file or URL) and run one real
// query end to end.
//
// Usage: node test/smoke_test.mjs <path-or-url-to-wheel> <expected-version>
//
// <expected-version> may be "*" to skip the exact-version assertion while still
// requiring the wheel to install, import and answer a query. That is for building
// from a BRANCH (e.g. a pre-release line), where the wheel's own version string is
// not derivable from the ref. A release build always passes a real version.
import { loadPyodide } from "pyodide";
import fs from "fs";

const [, , wheelArg, expectedVersion] = process.argv;
if (!wheelArg || !expectedVersion) {
  console.error("Usage: node test/smoke_test.mjs <path-or-url-to-wheel> <expected-version>");
  process.exit(1);
}

const pyodide = await loadPyodide();
await pyodide.loadPackage(["micropip"]);

let installTarget = wheelArg;
if (!wheelArg.startsWith("http://") && !wheelArg.startsWith("https://")) {
  const bytes = fs.readFileSync(wheelArg);
  const name = wheelArg.split("/").pop();
  pyodide.FS.writeFile(`/${name}`, bytes);
  installTarget = `emfs:/${name}`;
}

let failed = false;
try {
  await pyodide.runPythonAsync(`
import micropip
await micropip.install("${installTarget}")

import duckdb
${expectedVersion === "*" ? "print(f\"version assertion skipped (expected-version '*'), wheel reports {duckdb.__version__!r}\")" : `assert duckdb.__version__ == "${expectedVersion}", f"expected duckdb {${JSON.stringify(expectedVersion)}!r}, got {duckdb.__version__!r}"`}

con = duckdb.connect()
rows = con.execute("SELECT 1 + 1 AS two").fetchall()
assert rows == [(2,)], f"unexpected query result: {rows}"

print(f"duckdb {duckdb.__version__} installed and working under Pyodide")
`);
  console.log("SMOKE TEST PASSED");
} catch (e) {
  failed = true;
  console.error("SMOKE TEST FAILED:");
  console.error(e.message);
}

process.exit(failed ? 1 : 0);
