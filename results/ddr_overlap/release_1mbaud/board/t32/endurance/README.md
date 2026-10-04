# T32 1 Mbaud endurance

Continuous mixed workload in both modes on build `0x9d4beb4d`, P8/T32/READ4 at 100 MHz.
The completed units contain 592 jobs and
550,634 full INT32 output comparisons. Every job initializes
C afresh and checks the entire guarded A, BT and C allocations.

The original qualification stopped after a register-read timeout. A Windows
Modern Standby wake event shortly beforehand supports host sleep interruption
as a likely cause. Its actual native exit 1 and stage failure remain
preserved. Read-only reconnect verified retained job 384 without reset,
reprogramming or replaying START. The new endurance began from zero in one
connection under temporary Windows sleep inhibition; interrupted duration was
never carried forward. The actual recovered parent and child exits were 0.
Previously sealed maximum and dense units were copied unchanged and were not
rerun. Their original times and seal identities remain explicit.

Inputs stay resident in DDR between the paired samples for each prepared case.
Reported JOB_CYCLES includes all accelerator DDR transfers and final completion.
Packing, oracle construction and initial input upload are recorded once per case;
C initialization, configuration, job wall time and full-allocation download are
recorded per job. Validation time is separate. COMPUTE_CYCLES counts scheduled
microtiles; JOB_CYCLES minus COMPUTE_CYCLES is remaining job time, not a measured
DDR bandwidth or isolated load/store partition.

`records.tar.gz` preserves all original unit seals, case/job records and aggregate
JSON/CSV bytes. `results.json`, `results.csv` and `summary.json` are independently
derived aggregates. No per-job raw C or UART stream was retained for these phases,
so replay validates metadata, digests, exact counters, seeds, pairing and execution
provenance. It cannot reproduce those original numerical comparisons from bytes.
The separate read-only recovery includes its three retained DDR snapshots and
supports a narrow independent 64-output and guard replay. No warm-reset or DDR
electrical qualification is claimed.

```text
python -O verify.py --check-current --repo PATH_TO_CHECKOUT
python -O verify.py --extract-records PATH_TO_FRESH_DIRECTORY
```

These commands are software-only and never contact hardware. The general public
qualification runner remains `scripts/qualify_release.py`; the preserved method
snapshots document the exact measured execution and recovery history.
