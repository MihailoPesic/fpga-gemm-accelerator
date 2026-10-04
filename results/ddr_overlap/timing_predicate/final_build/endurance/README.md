# Current-image mixed-workload endurance

The Nexys A7-50T completed **368 jobs without mismatches** in one uninterrupted
USB-UART connection: 184 in MODE=0 and 184 in MODE=1. The run lasted
**1,831.469 seconds (30 minutes 31.469 seconds)** and completed 23 full cycles
of the eight shapes below on image `0x2c680af7`, VERSION `0x200`, P8/T32,
four outstanding reads, 100 MHz and 115200 baud.

| M | N | K | Completed jobs per mode |
| ---: | ---: | ---: | ---: |
| 1 | 1 | 1 | 23 |
| 5 | 3 | 9 | 23 |
| 31 | 33 | 17 | 23 |
| 33 | 35 | 256 | 23 |
| 65 | 63 | 255 | 23 |
| 1 | 64 | 256 | 23 |
| 64 | 1 | 256 | 23 |
| 32 | 32 | 256 | 23 |

Every job compared all useful C values against a NumPy INT64 reference and
checked the complete guarded A, BT and C allocations. The total was
**342,286 output elements** and **5,319,624 input/padding/guard bytes**;
6,688,768 allocation bytes were checked when useful C bytes are also counted.
These counts include repeated checks across jobs. UART retries and rejected
frames were both zero.

Each case used a new deterministic seed. Its two modes used identical input
bytes and freshly initialized C padding and guards. Mode order alternated
across cases and cycles. A and BT remained in DDR for the two jobs in a case;
the next case uploaded new inputs. The elapsed interval includes host
preparation, USB-UART transfers, FPGA jobs, validation and record writes.
This is a continuous host-paced repeatability test, not 30 minutes of saturated
array execution or a lifetime reliability qualification.

The test ran from 13:54:31 to 14:25:03 UTC on 4 October 2026. It followed the
[maximum-shape check](../maximum/README.md) in the
[qualified powered session](../board/README.md); the runner did not program,
reset or reconnect the board. Warm reset remains outside this evidence.
The exact bitstream SHA-256 is
`5124adc4348ed1f42b6420d2776d7d1f50a44df7e340a6789b3a9954373e4cf5`.

[results.json](results.json) and [results.csv](results.csv) retain every job's
descriptor, counters, timings and data digests. [plan.json](plan.json) contains
the original plan; its maximum and full-grid benchmark settings were not
executed by this endurance-only invocation. [summary.json](summary.json)
preserves that scope. [execution.json](execution.json),
[native_receipt.json](native_receipt.json) and [console.txt](console.txt) bind
the actual zero exits and complete console. [method.py](method.py) retains the
executed wrapper, including its maximum-test completion gate. [build.json](build.json) retains
the as-built source commit and dirty state. The frozen host and runner sources
are under [inputs](inputs/); current source hashes were independently checked
at curation in [source_verification.json](source_verification.json).

[records.tar.gz](records.tar.gz) losslessly stores the 184 original case records
and 368 job records. [archive_inventory.json](archive_inventory.json) gives
their raw sizes and SHA-256 hashes. [original_seal.json](original_seal.json)
binds those records and the original aggregate files; [manifest.json](manifest.json)
seals this compact publication. Raw matrix snapshots and a UART transcript were
not saved in this run. The standalone verifier checks the saved records,
hashes, mode pairing, counter totals and execution consistency; it cannot
independently replay output bytes or wire traffic.

Verify all retained bytes and independently enumerated tile traffic without
accessing hardware:

```sh
python results/ddr_overlap/timing_predicate/final_build/endurance/verify.py
```

To also check current RTL/host hashes, add `--check-current`. To decompress and
verify every original record into a fresh workspace directory, use:

```sh
python results/ddr_overlap/timing_predicate/final_build/endurance/verify.py --extract-records build/endurance_records_verified
```

Reproduce on the matching programmed image with a fresh output directory:

```sh
python scripts/qualify_release.py --port COM11 --manifest build/gemm_p8_overlap_final/build.json --output build/endurance_check --phase endurance --modes both --oracle numpy --duration 1800
```
