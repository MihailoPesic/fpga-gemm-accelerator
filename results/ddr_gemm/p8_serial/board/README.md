# P8 serial DDR GEMM: board qualification

Build `0x01caf61c` passed all 48 physical jobs: 49,593 outputs and 689,628
input, padding and guard bytes checked in 221.700 seconds.
The image uses P8/T32 at 100 MHz, serial scheduling and 115200-baud UART.
There were zero transport retries, rejected frames or poisoned sessions.

| Case | M x N x K | Jobs | Job cycles: min / median / max |
| --- | --- | ---: | ---: |
| signed_scalar | 1 x 1 x 1 | 3 | 216 / 217 / 220 |
| signed_extremes | 1 x 2 x 256 | 3 | 932 / 933 / 940 |
| odd_tail | 5 x 3 x 9 | 3 | 823 / 824 / 828 |
| column_tile_boundary | 31 x 33 x 17 | 3 | 12493 / 12500 / 12518 |
| multi_tile_kmax | 33 x 35 x 256 | 3 | 40087 / 40101 / 40133 |
| asymmetric_k255 | 65 x 63 x 255 | 3 | 100608 / 100653 / 100709 |
| resident_dense | 32 x 32 x 256 | 30 | 21195 / 21281.5 / 21366 |

Each case uploads its inputs once; later repetitions reuse DDR contents.
Every output and all input allocations, C padding and allocation guards are
checked after every job. The dense case has 30 completed repetitions.
JOB_CYCLES includes tile transfers and the final successful write response.
Wall time additionally includes slow UART transfers, validation and persistence.

The operator confirmed a fresh OFF/ON cycle before this image was programmed.
[cold_start_confirmation.json](cold_start_confirmation.json) and
[program.log](program.log) record startup and selected JTAG image; UART reports
the matching BUILD_ID. This does not constitute configuration hash readback or
warm-reset qualification. The configured Micron MIG preset remains distinct
from the reported ISSI memory marking.

[manifest.json](manifest.json) binds all 21 byte-identical raw records, the six
host inputs and 28 build inputs. Matching [vendor](../vendor/README.md) and
[routed](../routed/README.md) records retain their own gates and warning evidence.
The [P4/P8 comparison](../../p8_scaling/README.md) uses the same host sources,
shapes, seeds, packed input bytes and transfer counts.

This bounded suite is not a maximum-shape test, 30-minute P8 repeatability run,
full DDR address sweep, overlap implementation or lifetime reliability claim.

```text
python scripts/hw_test_ddr_gemm.py --p 8 --port COMx --manifest build/gemm_p8_serial/build.json --output <fresh-directory> --retries 0
```
