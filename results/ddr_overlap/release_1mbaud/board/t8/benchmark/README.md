# T8 serial baseline at 1 Mbaud

Build `0xeed7b111`, P8/T8/READ4 at 100 MHz, runs the full 16-case grid in
serial MODE0. Each case has 30 checked samples: 480 jobs and
7,990,980 complete INT32 output comparisons. All A, BT and C
allocations are checked, including input bytes, row padding and guards.
This archive qualifies this MODE0 workload; it makes no T8 maximum-shape,
endurance or physical MODE1 claim.

The operator confirmed a separate cold power cycle after the T32 process had
completed with a genuine native exit 0. Programming, smoke, dense and full-grid
stages all completed with actual exit 0 on the exact source-matched image.
The dense case was measured by the first benchmark child; the full-grid child
skipped its intact seal and measured the remaining cases. Both child processes
start their JOB_ID sequence at 1, so IDs are interpreted with unit paths and
execution epochs rather than as a globally unique sequence.

Inputs stay resident in DDR for each prepared case. C is reinitialized before
every sample. JOB_CYCLES includes accelerator DDR transfers and the final write
acknowledgement; packing, oracle construction, initial input upload and per-job
host phases are recorded separately. Validation time is separate. Remaining job
cycles after COMPUTE_CYCLES are not isolated measured DDR load/store bandwidth.

`records.tar.gz` preserves 544 original files: each case record, all job records,
original JSON/CSV aggregates and original PASS seals. `results.json`,
`results.csv` and `summary.json` are independently derived readable aggregates.
There are no retained per-job C snapshots or UART stream, so the standalone
validator checks metadata, digests, counters, traffic/schedule arithmetic,
seeds, C sentinels, execution receipts and source/image identities. It cannot
replay the original numerical comparisons from saved matrix bytes. Warm-reset
and DDR electrical qualification remain outside this record.

```text
python -O verify.py --check-current --repo PATH_TO_CHECKOUT
python -O verify.py --extract-records PATH_TO_FRESH_DIRECTORY
```

Validation is software-only. The supported measurement interface remains
`scripts/qualify_release.py`; method snapshots retain this actual execution.
