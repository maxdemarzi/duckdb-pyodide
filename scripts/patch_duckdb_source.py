"""Patch DuckDB's own sources in the duckdb-python checkout before the wheel is built.

Run from the checkout root (build/duckdb-python), after patch_cmake.py.

THE PERFECT-HASH AGGREGATE REGRESSION IN v2.0.0
-----------------------------------------------
v1.5.4's PhysicalPerfectHashAggregate::Sink ended its setup with

    group_chunk.SetCardinality(chunk.size());
    aggregate_input_chunk.SetCardinality(chunk.size());

v2.0.0 removed both lines. A DataChunk with no explicit count derives its size
from its vectors: size() is `count.IsValid() ? count.GetIndex() : DeriveSize()`,
and DeriveSize() is deliberately out of line and walks the chunk's columns.
PerfectAggregateHashTable::AddChunk then loops

    for (idx_t i = 0; i < groups.size(); i++)

so the walk happens once PER ROW. Natively that costs about 1.3x; under wasm it
is a fixed ~90 ns/row, so a low-cardinality GROUP BY over 3M rows goes from
54 ms (1.5.4) to 285 ms (2.0.0), whatever the number of groups or aggregates.

Two edits, either of which fixes the loop; both are made because DeriveSize is
also reached from D_ASSERT/Verify and any later size() call on the chunk:

  1. physical_perfecthash_aggregate.cpp: restore the SetCardinality calls.
  2. perfect_aggregate_hashtable.cpp: read groups.size() once before the loop.

Every edit asserts the exact text it replaces and fails the build if it is not
there, rather than silently shipping an unpatched wheel. If a later DuckDB fixes
this upstream, the assertion trips and this step should be deleted.
"""
import pathlib
import sys

SRC = pathlib.Path("external/duckdb/src")


def replace_once(path: pathlib.Path, old: str, new: str, what: str) -> None:
    text = path.read_text()
    if new in text:
        print(f"{path}: {what} already applied -- nothing to do.")
        return
    if text.count(old) != 1:
        sys.exit(f"ERROR: {path}: expected exactly one occurrence of the text for "
                 f"'{what}', found {text.count(old)}. DuckDB changed; refusing to guess. "
                 "If upstream fixed the regression, delete this patch step.")
    path.write_text(text.replace(old, new, 1))
    print(f"Patched {path}: {what}.")


def main() -> None:
    replace_once(
        SRC / "execution/operator/aggregate/physical_perfecthash_aggregate.cpp",
        "\tgroup_chunk.Verify(context.client.db);\n\taggregate_input_chunk.Verify(context.client.db);\n",
        "\tgroup_chunk.SetCardinality(chunk.size());\n"
        "\taggregate_input_chunk.SetCardinality(chunk.size());\n"
        "\tgroup_chunk.Verify(context.client.db);\n\taggregate_input_chunk.Verify(context.client.db);\n",
        "restore SetCardinality on the group and aggregate-input chunks (removed in v2.0.0)",
    )
    replace_once(
        SRC / "execution/perfect_aggregate_hashtable.cpp",
        "\tfor (idx_t i = 0; i < groups.size(); i++) {\n\t\tconst auto group = address_data[i];",
        "\tconst idx_t groups_count = groups.size();\n"
        "\tfor (idx_t i = 0; i < groups_count; i++) {\n\t\tconst auto group = address_data[i];",
        "read groups.size() once before the per-row loop instead of once per row",
    )


if __name__ == "__main__":
    main()
