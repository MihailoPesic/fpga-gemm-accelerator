# Row planner with selectable read depth

PASS: 10 test executions across `READ_SLOTS=1` and `READ_SLOTS=4`.
The independent oracle enumerates useful row-word addresses, then groups them
by page and burst size. It checks descriptor snapshots, every emitted address
and metadata field, and ordered completion obligations.

| Read slots | Tests | Valid descriptors | Invalid descriptors | Accepted bursts |
|---:|---:|---:|---:|---:|
| 1 | 5 | 271 | 121 | 4,607 |
| 4 | 5 | 279 | 121 | 4,650 |

Both configurations exercise lengths 1..16, row tails, exact DDR-window bounds,
4 KiB splits, held offers/completions, and error responses. The depth-four
cases additionally fill all credits, check nine simultaneous issue/retirement
edges, cancel with zero/two/four accepted commands, and drain accepted work
before DONE. Reset coverage discards the local responder epoch together with
the planner, including a mixture of accepted commands and an unaccepted offer.
The fifth test's queue-specific body runs only at depth four; the serial
configuration retains its existing fault tests. Accepted-burst totals include
work deliberately discarded by local reset.

This is a planner test with a local completion responder. It does not test
AXI data, bank ownership, DDR hardware, or resetting a physical memory system
with outstanding transactions. [Tile integration](../tile/README.md) adds the
real banked compute and AXI burst modules.

Reproduce both depths with the pinned test dependencies:

```text
python scripts/test_dma_rows.py --build-dir build/test_dma_rows_read4
```

[summary.json](summary.json), both configuration coverage/XML files, and
[console.txt](console.txt) are exact copies of the completed run.
[manifest.json](manifest.json) records their raw byte hashes and five source
fingerprints. The runner's before/after source hashes and the curation-time
checkout agree; no source commit or physical qualification is inferred from
those fingerprints. Earlier row-planner evidence remains unchanged.
