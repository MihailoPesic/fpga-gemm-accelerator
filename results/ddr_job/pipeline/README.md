# Serial jobs after timing changes

All four P/T builds pass the full portable job regression with registered
row validation, registered tile-end decisions and the prepared watchdog
threshold: 16 tests, 154 completed jobs, 103,086 compared outputs and 124
invalid descriptors rejected before DMA.

| P | T | Tests | Jobs | Compared outputs | Macrotiles |
| --- | --- | --- | --- | --- | --- |
| 4 | 8 | 4 | 38 | 5,789 | 366 |
| 4 | 32 | 4 | 39 | 42,912 | 158 |
| 8 | 8 | 4 | 38 | 5,337 | 361 |
| 8 | 32 | 4 | 39 | 49,048 | 164 |
| Total | | 16 | 154 | 103,086 | 1,049 |

The real descriptor controller, tile DMA, burst engine, banks and compute
engine use behavioral AXI RAM. The Python integer oracle checks every useful
result, input allocations, C padding and surrounding guards. Directed cases
include non-square tails, signed extrema, K=255/256, 1024x1x256 and
1x1024x256, adjacent allocations and the exact end of the DDR window.

Tests check final successful B timestamps, frozen counter identities,
START/response backpressure and nine fault scenarios per configuration.
These include memory-response errors, local/core protocol failures,
calibration loss and watchdog expiry with stalled AR or B obligations.

The suite includes 100 seeded jobs. It shares a seeded generator between
local stalls and later matrix generation, so changed RTL latency can change
the subsequent shape sequence. The exact executed shapes are retained in
each coverage file. This is a correctness rerun, not a controlled runtime
comparison with the earlier implementation.

[The summary](summary.json) merges four successful configuration records
only after all 17 source hashes and dependency versions match. Coverage and
XML are copied byte-for-byte; the summary records their hashes and the
original local summary/console hashes. [The console excerpt](console_excerpt.txt)
contains selected version and test-result lines. No test was filtered out.

```text
make test-ddr-job PYTHON=.venv/bin/python
```

This evidence covers the current portable controller integration. The
[original regression and isolated timing probe](../README.md) retain their
historical source identities. Vendor integration, full-board routed timing,
physical DDR GEMM, overlap and concurrent reads are separate qualifications.
