# P4 serial DDR GEMM repeatability

Image `0x4898db67` completed **9 full passes** of the unchanged
48-job hardware plan over **1999.297869600 seconds (33.322 minutes)**.
Every pass completed before the runner stopped. Across all passes,
**432 jobs** compared
**446,337 outputs** and checked
**6,206,652 input/padding/guard bytes**.
There were no transport retries, rejected frames, poisoned sessions, mismatches
or failed jobs in the saved records.

| Repeated workload | Completed jobs | Cycles min/median/max |
| --- | ---: | ---: |
| signed_scalar | 27 | 203/204/210 |
| signed_extremes | 27 | 919/924/952 |
| odd_tail | 27 | 835/839/855 |
| column_tile_boundary | 27 | 13892/13913/13974 |
| multi_tile_kmax | 27 | 54958/55022/55174 |
| asymmetric_k255 | 27 | 153599/153760/153965 |
| resident_dense | 270 | 34020/34100.0/34210 |

The seven deterministic matrix pairs and seeds repeat across passes; these counts
include repeated comparisons, not that many distinct matrix inputs or DDR locations.
Each case uploads once and reuses its DDR inputs within that case. Every job
downloads and compares every output, then checks complete input allocations,
C padding and guards. Each pass opens a new identified UART connection and
reloads inputs; no failed operation is reset or retried by the outer runner.

This is **host-paced repeatability**, not continuous compute stress. The measured
wall time includes UART transfers at 115200 baud, Python validation, guard
readback, result writes and process overhead. The sum of frozen DDR-resident
job intervals is 0.152748810 seconds, separately from the total elapsed
time above. This result is not a full-DDR sweep, a lifetime reliability claim,
or warm-reset qualification.

The P4/T32 image runs at 100 MHz. Its bitstream SHA-256 is
`55eafc4dba5053b12d8299165b6d2deddb2dea9e7fb76d4ec6b4b4403cf1b910`. [The board qualification](../board/README.md) holds the matching
qualified manifest, 28 input hashes, cold-start confirmation and JTAG program log.
This run continued that powered/programmed session; the cold-start record is
inherited rather than a new power-cycle confirmation for each pass. The as-built
source state was dirty, so the exact source hashes identify the image.

[summary.json](summary.json) is the unchanged parent record. Each `pass_XX`
directory contains the complete combined `results.json`, `results.csv`,
`summary.json`, and original child console. All per-case duplicates were checked
against these combined rows during curation. [manifest.json](manifest.json)
records byte hashes, source/build seals, aggregate counters and the original
orchestration hash. The private launcher is not part of the hardware design.

The workload can be reproduced by repeatedly running the public command below,
using a fresh output directory for every pass, until measured monotonic elapsed
time reaches at least 1800 seconds. Complete the final pass and stop immediately
on any nonzero exit. Use the matching qualified manifest and programmed image:

```text
python scripts/hw_test_ddr_gemm.py --port COMx --manifest build/gemm_gather4/build.json --output <fresh-pass-directory> --retries 0
```
