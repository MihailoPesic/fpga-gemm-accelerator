# T32 maximum shape at 1 Mbaud

The Nexys A7-50T completed M=N=1024,K=256 in both MODE=0 and MODE=1 on
P8/T32 image `0x9d4beb4d`, four read slots and a 100 MHz core. The host checked
all **2,097,152 INT32 outputs**, 9,437,952 allocation bytes and 1,049,344
input/guard bytes, with zero transport retries or rejected frames.

| Mode | Job cycles | DDR-job time | Useful GOPS |
| --- | ---: | ---: | ---: |
| Serial | 11,863,408 | 118.63408 ms | 4.525436 |
| Overlap | 4,664,784 | 46.64784 ms | 11.509020 |

These are **one completed job per mode**, not repeated benchmark distributions.
Both jobs use identical A/BT bytes and freshly initialized identical C sentinels.
Each job accepts 2,097,152 read beats and 524,288 write beats, writes 4,194,304
useful C bytes and spends 4,571,136 cycles in active microtile schedules.
JOB_CYCLES includes every tile load, result write and final successful B
response. Packing, USB-UART movement, polling and validation are separate.

The [original unit](unit/results.json), [CSV](unit/results.csv) and
[original seal](unit/seal.json) retain their bytes. Both job folders retain
complete gzip snapshots of A, BT, C after the job, C before the job and the
wide INT32 oracle. This permits complete numerical and guard replay.
[replay.json](replay.json) records a separate INT64 `A @ BT.T` recomputation
from retained actual DDR inputs, matching both saved oracles and every C byte.
No UART stream was retained, so this is not a wire-level replay.

The operator cold-power receipt, actual programming marker, image hashes,
completed stage receipts and exact executed wrapper are preserved.
The original program, smoke and dense stages exited zero; its later
full-qualification stage and outer process exited **one**. Endurance ended
with a READ_REG transport timeout after this independently sealed maximum
unit had completed. This archive preserves that failed history and reports
the maximum unit only. It does not claim successful endurance or full-grid
qualification. The [implementation/static archive](../../../t32/README.md)
retains the exact image's routed and vendor gates. Warm reset remains unqualified.

The maximum unit was copied byte-for-byte into the resumed measurement tree.
[resume_copy.json](resume_copy.json) checks every copied artifact against the
original. That copy is not a second maximum run and makes no claim about the
resumed parent's completion.

[manifest.json](manifest.json) seals every saved file. [build.json](build.json)
retains the original source commit and dirty state; [sources.tar.gz](sources.tar.gz)
preserves all 32 build inputs and six host/runner inputs, with raw and normalized
hashes in [source_inventory.json](source_inventory.json). Source snapshots and
large stage logs use lossless deterministic gzip. Generated bitstreams are
kept in the local build archive rather than this evidence directory.

Run the standalone replay without hardware (requires NumPy):

```text
python -O results/ddr_overlap/release_1mbaud/board/t32/maximum/verify.py --check-current
```

Omit `--check-current` when reviewing a copied archive outside the checkout.
The verifier checks every original compressed and decompressed snapshot hash,
original seal, seed/layout, mode order, complete inputs/padding/guards, useful
outputs, independently enumerated traffic, CSV and successful/failed stage receipts.

With the exact qualified image already programmed after a cold power-up, the
supported rerun is:

```text
python scripts/keep_awake.py -- python scripts/qualify_release.py --port COM11 --manifest build/gemm_release_p8_t32_1mbaud/build.json --phase maximum --modes both --oracle numpy --seed 20261004 --output build/new_maximum_check
```

Use a fresh output directory and the board's actual serial port. This command
never programs or resets the FPGA. The wrapper temporarily inhibits idle sleep
for the child command; it does not change the Windows power plan.
