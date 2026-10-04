# P4 baseline for array scaling

Build `0x4898db67` completed all 48 jobs with the shared P4/P8 qualification
runner: 49,593 outputs and 689,628 input, padding and guard bytes checked in
221.921 seconds. UART recorded zero retries, rejected
frames or poisoned sessions. Geometry remains P4/T32 at 100 MHz and 115200 baud.

| Case | M x N x K | Jobs | Job cycles: min / median / max |
| --- | --- | ---: | ---: |
| signed_scalar | 1 x 1 x 1 | 3 | 203 / 204 / 204 |
| signed_extremes | 1 x 2 x 256 | 3 | 919 / 926 / 928 |
| odd_tail | 5 x 3 x 9 | 3 | 835 / 836 / 843 |
| column_tile_boundary | 31 x 33 x 17 | 3 | 13902 / 13915 / 13940 |
| multi_tile_kmax | 33 x 35 x 256 | 3 | 54958 / 54985 / 54992 |
| asymmetric_k255 | 65 x 63 x 255 | 3 | 153692 / 153768 / 153788 |
| resident_dense | 32 x 32 x 256 | 30 | 34025 / 34103.0 / 34241 |

Each case uploads once and retains its DDR inputs for subsequent repetitions.
The dense case contains 30 completed runs. Every result and the complete input
allocations, C padding and allocation guards are checked after every job.
JOB_CYCLES includes the DDR-backed job through its final successful write
response. Wall time also includes UART transfers, validation and result saving;
it is not continuous compute time.

This repeats the original seven shapes, seeds and signed matrix patterns with
the runner's explicit `--p 4/8` selection. The other five host inputs are unchanged.
[host_identity.json](host_identity.json) records the exact old/new runner hashes,
the shared six-source identity, unchanged P4 plan hash and seven matrix byte hashes.
The public test source pins the workload and matrix byte hashes.

The image stayed in the same powered session following the previously recorded
[cold startup and programming](../../gather4/board/README.md). This run claims no
additional power cycle. [manifest.json](manifest.json) verifies and references those
existing build, input, cold-start and program records by exact hashes.

[results.json](results.json), [results.csv](results.csv) and [summary.json](summary.json)
are unchanged combined hardware records. All fourteen per-case originals were
also verified; their hashes are retained in the manifest without duplicate copies.
This is the P4 baseline for a subsequent P8 comparison. It establishes no physical
P8 speedup, new maximum-shape test, warm-reset qualification or DDR address sweep.

Reproduce after programming the matching qualified image:

```text
python scripts/hw_test_ddr_gemm.py --p 4 --port COMx --manifest build/gemm_gather4/build.json --output <fresh-directory> --retries 0
```
