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

THE TINY HELPERS CLANG DID NOT INLINE
-------------------------------------
In the wasm build, a call from a side module to a weak (linkonce_odr) function
that was not inlined is an `env` import, and Emscripten cannot bind a module's
own exports at instantiate time: each such call is a JS closure with rest-args
forwarding and i64<->BigInt conversion (Builtins_CallForwardVarargs,
I64ToBigInt). 64,023 of the 64,535 function imports in the v2.0.0 wheel are
functions the same module exports. DuckDB 2.0's hot paths call several one-line
helpers that stayed out of line: MurmurHash32/64 (distinct and any hash
aggregate), vector<T>::get/back/AssertIndexInBounds and unique_ptr/optional_ptr
accessors (the PEG parser: parse-only of a 60-column CASE goes 4.6 -> 97 ms).
`__attribute__((always_inline))` on exactly those helpers removes the call.
"""
import pathlib
import re
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


INLINE = "__attribute__((always_inline)) "


def force_inline(path: pathlib.Path, needles: list, what: str) -> None:
    """Prefix each declaration in `needles` (exact text, each expected at least
    once) with always_inline. Idempotent, and fails loudly on a missing needle."""
    text = path.read_text()
    for needle in needles:
        if INLINE + needle in text:
            continue
        n = text.count(needle)
        if n < 1:
            sys.exit(f"ERROR: {path}: declaration for '{what}' not found: {needle!r}. "
                     "DuckDB changed; refusing to guess.")
        text = text.replace(needle, INLINE + needle)
    path.write_text(text)
    print(f"Patched {path}: always_inline on {what}.")


def force_inline_hot_helpers() -> None:
    inc = SRC / "include/duckdb/common"
    force_inline(inc / "types/hash.hpp",
                 ["inline hash_t MurmurHash64(", "inline hash_t MurmurHash32(", "inline hash_t CombineHash("],
                 "MurmurHash32/64 and CombineHash")
    force_inline(inc / "vector.hpp",
                 ["static inline void AssertIndexInBounds(",
                  "\tinline typename original::reference get(",
                  "\tinline typename original::const_reference get(",
                  "\ttypename original::reference operator[](",
                  "\ttypename original::const_reference operator[](",
                  "\ttypename original::reference front()",
                  "\ttypename original::const_reference front() const",
                  "\ttypename original::reference back()",
                  "\ttypename original::const_reference back() const"],
                 "vector<T>::get/operator[]/front/back/AssertIndexInBounds")
    force_inline(inc / "unique_ptr.hpp",
                 ["static inline void AssertNotNull(",
                  "\ttypename std::add_lvalue_reference<DATA_TYPE>::type operator*() const",
                  "\ttypename original::pointer operator->() const"],
                 "unique_ptr accessors")
    force_inline(inc / "optional_ptr.hpp",
                 ["\tvoid CheckValid() const {", "\tT &operator*() {", "\tconst T &operator*() const {",
                  "\tT *operator->() {", "\tconst T *operator->() const {"],
                 "optional_ptr accessors")
    force_inline(inc / "optional_idx.hpp",
                 ["\tbool IsValid() const {", "\tidx_t GetIndex() const {"],
                 "optional_idx accessors")


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
    force_inline_hot_helpers()


if __name__ == "__main__":
    main()
