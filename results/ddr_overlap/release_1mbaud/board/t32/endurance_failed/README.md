# T32 stopped endurance run at 1 Mbaud

This run **failed** and does not qualify 30-minute endurance or the full benchmark grid.
On image `0x9d4beb4d`, 381 jobs completed full host comparisons before an opcode-2
register-read timeout stopped job 384. The preserved successful prefix contains
190 serial and 191 overlap jobs, 355,056 checked INT32 outputs, 6,916,736 checked
allocation bytes and 5,496,512 input/guard bytes. It includes 23 whole mixed cycles
and part of the next cycle; those are partial progress, not a successful release gate.

Job 384 was serial 64x1x256. Its frozen compute/DDR counters were already read,
but its stage is DOWNLOAD and no final snapshot/validation totals were saved.
The host checks identity both before downloads and after byte comparisons;
without UART data the saved record cannot determine which READ_REG timed out.
It records an uncertain transport outcome, not a numerical mismatch or proof
that computation stalled. No result from this job is counted as validated.

The final unit record has state FAIL and no PASS seal. Its stored continuous
progress value, 1145.1762048 seconds, is the last successfully persisted case
progress before the timeout; it is not the complete stopped wall duration.
The original transport counters are an earlier successful snapshot, so their
zero retry/rejection values do not override the recorded timeout.

The actual outer native exit was 1; program, smoke and dense stages exited 0,
while full qualification exited 1. Actual cold-power confirmation, programming
marker, exact wrapper, 32 build inputs and six host/runner inputs are preserved.
Earlier completed dense and maximum units remain separate evidence. Warm reset,
DDR electrical behavior are not qualified here.

The preserved Windows event record shows an exit from Modern Standby about
three seconds before the timeout. This supports host sleep interruption as a
likely cause; it does not identify the failed register-read site or prove cause.
After a read-only reconnect, the same image reported READY and DONE for job
384 with unchanged counters. All 64 retained outputs and all 21,120 allocation
bytes were compared successfully, including input bytes, C padding and guards.
No reset, reprogramming or START replay occurred. This later recovery is separate
from the failed endurance run and does not add a completed job to its counts.

`records.tar.gz` preserves all 575 original files, including the failed job.
`original_results.json` and `original_results.csv` retain readable original bytes.
`manifest.json` seals this failure archive; it does not create a producer PASS seal.
No raw matrix snapshots or UART stream were retained during this endurance unit,
so its prefix is checked from saved records, digests, counters and provenance.
The separate recovery preserves three compressed DDR snapshots and its genuine
native exit-0 receipt. The validator independently recomputes that one 64-output
dot product from signed INT8 bytes and compares the entire recovered C allocation.

```text
python -O verify.py --check-current --repo PATH_TO_CHECKOUT
python -O verify.py --extract-records PATH_TO_FRESH_DIRECTORY
```

Successful validator output means the failed evidence is consistent, not that
the accelerator passed endurance. No validation step contacts or resets hardware.
