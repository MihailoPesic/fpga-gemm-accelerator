# T32 dense benchmark at 1 Mbaud

The Nexys A7-50T completed 60 DDR-backed INT8 GEMM jobs for M=N=K=256:
30 matched serial/overlap pairs on image `0x9d4beb4d`. The host compared
3,932,160 INT32 outputs and checked 23,616,000 allocation bytes, including
7,887,360 input/guard bytes. There were zero transport retries or rejected frames.

| Mode | Minimum cycles | Median cycles | Maximum cycles | Median useful GOPS |
| --- | ---: | ---: | ---: | ---: |
| Serial | 740,890 | 741,063.5 | 741,391 | 4.527875 |
| Overlap | 296,951 | 297,001 | 297,058 | 11.297751 |

Median paired cycle speedup was 2.495140x. Both modes used the same inputs,
fresh identical C initialization, P=8, T=32, four read slots and 100 MHz clock.
Each job transferred 131,072 read beats and 32,768 write beats, wrote 262,144
useful result bytes and spent 285,696 cycles in active microtile schedules.
JOB_CYCLES includes DDR tile transfers and the final successful write response.

![Dense throughput and recorded cycle totals](performance.png)

The [PDF figure](performance.pdf) uses all 30 paired samples. Its remaining
cycles are JOB_CYCLES minus active microtile cycles, not a measured DDR
load/store or bandwidth partition. [plot.py](plot.py) renders with the headless
Agg backend; optional package versions are saved in
[requirements-benchmark.txt](inputs/requirements-benchmark.txt).

A and transposed B were uploaded once and remained in DDR for this case.
Every job reinitialized C and read each full A/BT/C guarded allocation once.
UART upload/download and validation are excluded from FPGA GOPS; their separate
times and the initial preparation/upload cost are retained in [summary.json](summary.json).
Sample order alternated between modes. This isolates overlap on one build;
it is not the separate T8/T32 reuse comparison or a host-inclusive speedup.

[results.csv](results.csv) and [results.json](results.json) are original bytes.
[records.tar.gz](records.tar.gz) contains all 61 original case/job JSON records;
the verifier reconstructs the original 63-file seal. Completed program, smoke
and dense-stage exit receipts are frozen separately. The wrapper snapshot may
show a subsequent qualification stage still running; this archive makes no
completion claim for that stage, the full grid, maximum shapes or endurance.

The cold-start receipt records the operator's actual OFF/ON confirmation.
The image is bound to the [implementation/static archive](../../../t32/README.md),
its source and bitstream hashes, own checkpoint review and observed UART identity.
The saved build/static receipts retain their original pre-board scope wording.
Warm reset and DDR electrical behavior are not qualified by this dataset.

Run the standalone evidence check without hardware:

```text
python -O results/ddr_overlap/release_1mbaud/board/t32/dense/verify.py --check-current
python -O results/ddr_overlap/release_1mbaud/board/t32/dense/verify.py --extract-records build/dense_records
python results/ddr_overlap/release_1mbaud/board/t32/dense/plot.py --output build/dense_plot
```

Extraction accepts any fresh destination. A copied archive works independently;
use `--check-current --repo PATH` to bind it to a particular checkout.
Raw matrix snapshots and UART traffic were not retained, so validation checks
saved metadata, counters, digests and execution consistency; it cannot replay
the original full numerical comparison or inspect the final AXI B edge.
The supported hardware rerun interface is `scripts/qualify_release.py` with
`--phase benchmark --cases 11 --samples 30 --modes both` and the same image manifest.
`method.py` is the exact orchestration wrapper snapshot retained for provenance;
it is not invoked by the validator.
