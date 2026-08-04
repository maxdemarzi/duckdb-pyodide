// Fresh-environment smoke test for a built duckdb Pyodide wheel: no repo
// checkout, no compiler -- install the wheel exactly the way a real
// consumer would (micropip, from a local file or URL) and run one real
// query end to end.
//
// Usage: node test/smoke_test.mjs <path-or-url-to-wheel> <expected-version>
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
assert duckdb.__version__ == "${expectedVersion}", f"expected duckdb {${JSON.stringify(expectedVersion)}!r}, got {duckdb.__version__!r}"

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
