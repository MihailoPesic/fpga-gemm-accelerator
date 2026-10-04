# READ4 P8 serial DDR GEMM: board qualification

Build `0xd558a543` passed 48 physical jobs, comparing 49,593 outputs and
689,628 input, padding and guard bytes at P8/T32, 100 MHz and 115200 baud.
Scheduling remains serial. All seven workloads completed with zero transport
retries, rejected frames or poisoned sessions.

| Case | M x N x K | Jobs | Job cycles: min / median / max |
| --- | --- | ---: | ---: |
| signed_scalar | 1 x 1 x 1 | 3 | 214 / 215 / 220 |
| signed_extremes | 1 x 2 x 256 | 3 | 611 / 616 / 625 |
| odd_tail | 5 x 3 x 9 | 3 | 475 / 475 / 476 |
| column_tile_boundary | 31 x 33 x 17 | 3 | 7329 / 7337 / 7342 |
| multi_tile_kmax | 33 x 35 x 256 | 3 | 19748 / 19752 / 19792 |
| asymmetric_k255 | 65 x 63 x 255 | 3 | 52318 / 52371 / 52411 |
| resident_dense | 32 x 32 x 256 | 30 | 11546 / 11576.5 / 11641 |

Each case uploads once, then reuses DDR inputs. Every output and complete input
allocations, C padding and allocation guards are checked after every job.
JOB_CYCLES includes DDR job transfers through the final successful B response;
host wall time also includes UART transfers, validation and persistence.

[manifest.json](manifest.json) binds all raw JSON/CSV, build/input seals,
[cold-start confirmation](cold_start_confirmation.json), the exact
[program.log](program.log), [selected JTAG evidence](program_excerpt.txt),
and both actual process exit records. The selected image hash and UART BUILD_ID
match. This is not FPGA configuration hash readback or warm-reset qualification.
Matching [vendor](../vendor/README.md) and [routed](../routed/README.md) evidence
retain the source, generated-platform, timing and production reset-review seals.

The P8 case plan matches the earlier one-read image 0x01caf61c, but DMA, burst handling and host control sources changed. This is a same-workload revision comparison, not an isolated READ_SLOTS experiment. A same-source READ1 board image is needed to isolate the effect of read depth.

This bounded suite does not establish maximum-shape performance, 30-minute
repeatability, a complete DDR address sweep, overlap or lifetime reliability.
The configured Micron MIG preset remains distinct from the reported ISSI
memory marking.
