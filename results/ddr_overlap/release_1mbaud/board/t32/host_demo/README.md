# T32 command-line and Python API smoke test

On 2026-10-08, a reported fresh OFF/ON power cycle with JP1 in JTAG mode
preceded programming of the qualified T32 image `0x9d4beb4d`. The native
runner and both child processes exited zero. The board then passed the
documented CLI and supplied-matrix Python API at 1 Mbaud.

| Check | Result |
| --- | --- |
| Image | P8/T32, four read slots, 100 MHz, VERSION `0x200` |
| CLI | MODE1, M=5,N=3,K=9, three repetitions; 45 outputs compared |
| CLI job cycles | 479, 480, 480; inputs uploaded once, then reused in DDR |
| Python API | M=2,N=2,K=3, one job per mode; eight output comparisons |
| API job cycles | MODE0: 264; MODE1: 265 |
| Memory checks | 1,156 input/padding/guard bytes per CLI job; 752 per API job |
| Transport | Zero retries and rejected frames; API connection unpoisoned |

The API supplied:

```text
A = [[1, -2, 3],       B = [[ 7,   8],
     [4,  5, -6]]          [-9,  10],
                           [11, -12]]

C = A * B = [[ 58, -48],
             [-83, 154]]
```

Every useful output and the guarded allocations were checked live. The
single-microtile example has no inter-tile work to overlap, so its two cycle
counts are a functionality check, not evidence of an overlap speedup. Use
the [dense comparison](../dense/README.md) for measured throughput.

The archive retains original execution and native-completion receipts,
programming/CLI stdout and stderr, CLI JSON/CSV, the build manifest and the
exact runner as `method.py`. Paths in that method describe the original
local execution; it is a historical method, not a standalone programming
entry point. Its source hashes and the CLI's embedded source snapshot bind
the host method. The qualified bitstream SHA-256 is
`d187f51bac37c3682641243f1db112579ad14c8ac45a1b3df52e704bf58c8327`;
the bitstream itself remains in the ignored local build archive.

The validator checks saved bytes, identities, counters, JSON/CSV agreement
and the saved API matrices against an independent integer dot-product
oracle. CLI matrices, raw UART frames and guard-memory snapshots were not
retained; it cannot independently replay their live numerical or byte
comparisons. A saved PASS and artifact hashes do not substitute for a new
physical board run.

From the repository root, with Python installed:

```text
python -B -O results/ddr_overlap/release_1mbaud/board/t32/host_demo/verify.py --area results/ddr_overlap/release_1mbaud/board/t32/host_demo --repo .
```

See the [host guide](../../../../../../docs/host-gemm.md) to use the current
image. Warm-reset DDR timing remains unqualified; programming requires a
fresh cold start. This small smoke test adds usage evidence to the measured
development release and does not qualify the full v1 specification.
