# Tile DMA after registered row validation

All four P/T configurations pass the complete portable tile-DMA regression
with the registered row-validation pipeline: 16 tests, 96 matrix jobs,
10,919 checked outputs and 64 rejected invalid requests.

| P | T | Tests | Jobs | Compared outputs |
| --- | --- | --- | --- | --- |
| 4 | 8 | 4 | 24 | 391 |
| 4 | 32 | 4 | 24 | 5,898 |
| 8 | 8 | 4 | 24 | 453 |
| 8 | 32 | 4 | 24 | 4,177 |

The fixture uses the production tile DMA, row sequencer, burst engine, banks
and compute engine with behavioral AXI RAM. The independent integer oracle
checks every useful result, guards and row padding. Tests include signed
extrema, odd and non-square shapes, K boundaries through 256, both buffer
IDs, independent local/AXI stalls and 16 fault/delay scenarios per build.

[The summary](summary.json) merges four successful runner records only after
their 15 source hashes and dependency versions match exactly. It links each
byte-for-byte coverage/XML copy, records saved artifact hashes, and retains
the original local summary and console hashes. [The excerpt](console_excerpt.txt)
contains selected version and test-result lines; the complete logs remain in
ignored build directories. No test was filtered out.

```text
make test-tile-dma PYTHON=.venv/bin/python
```

The [contract](../../../docs/tile-dma.md) defines ownership and failure behavior.
This result replaces the [original checkpoint](../README.md) as evidence for
the current row pipeline; original artifacts retain their historical hashes.
It does not qualify MIG, routed timing, board GEMM, overlap or concurrent reads.
