# Physical P8 revision comparison: READ1 to READ4

The [one-read image](../../p8_serial/board/README.md) `0x01caf61c` and
[four-read image](../host_geometry/board/README.md) `0xd558a543` each passed
48 physical jobs, 49,593 output comparisons and 689,628 input/padding/guard
byte checks. Both used the same seven shapes, seeds, guarded packed inputs,
P8/T32, 100 MHz, 115200 baud and serial scheduling on the same board.

| Case | READ1 cycles: min / median / max | READ4 cycles: min / median / max | Median cycle-time speedup | Useful GOPS at median cycles: READ1 / READ4 |
| --- | ---: | ---: | ---: | ---: |
| signed_scalar | 216 / 217 / 220 | 214 / 215 / 220 | 1.009302x | 0.000921659 / 0.000930233 |
| signed_extremes | 932 / 933 / 940 | 611 / 616 / 625 | 1.514610x | 0.109753483 / 0.166233766 |
| odd_tail | 823 / 824 / 828 | 475 / 475 / 476 | 1.734737x | 0.032766990 / 0.056842105 |
| column_tile_boundary | 12493 / 12500 / 12518 | 7329 / 7337 / 7342 | 1.703694x | 0.278256000 / 0.474062969 |
| multi_tile_kmax | 40087 / 40101 / 40133 | 19748 / 19752 / 19792 | 2.030225x | 1.474676442 / 2.993924666 |
| asymmetric_k255 | 100608 / 100653 / 100709 | 52318 / 52371 / 52411 | 1.921922x | 2.074900897 / 3.987798591 |
| resident_dense | 21195 / 21281.5 / 21366 | 11546 / 11576.5 / 11641 | 1.838336x | 2.463585743 / 4.528899063 |

The 32 x 32 x 256 dense workload has 30 observations per image. Median latency
changes from 21,281.5 cycles (212.815 microseconds) to 11,576.5 cycles
(115.765 microseconds), a 1.838336285x measured revision
speedup. Useful throughput derived from those median latencies is
2.463585743 versus
4.528899063 GOPS. Active compute remains
4,464 cycles; each run accepts 2,048 R beats, 512 W beats and 4,096 useful
write bytes. Dense median INPUT_WAIT changes from 13,172.5 to 3,468 cycles;
R-stall remains zero and W-stall remains 66 cycles.

| Case | READ1 INPUT_WAIT: min / median / max | READ4 INPUT_WAIT: min / median / max |
| --- | ---: | ---: |
| signed_scalar | 149 / 150 / 153 | 147 / 148 / 153 |
| signed_extremes | 609 / 610 / 617 | 289 / 294 / 303 |
| odd_tail | 582 / 583 / 587 | 234 / 234 / 235 |
| column_tile_boundary | 7047 / 7052 / 7072 | 1909 / 1909 / 1924 |
| multi_tile_kmax | 27973 / 27985 / 27989 | 7634 / 7645 / 7683 |
| asymmetric_k255 | 65760 / 65797 / 65853 | 17500 / 17508 / 17552 |
| resident_dense | 13098 / 13172.5 / 13233 | 3442 / 3468.0 / 3510 |

[comparison.json](comparison.json) retains every counter distribution,
cycle-time ratios, individual useful-GOPS distributions, and the exact 30
dense records from each source archive. [comparison.csv](comparison.csv)
contains seven summary rows and 30 descriptive repetition rows.
GOPS at median latency can differ slightly from the median of individual GOPS.
INPUT_WAIT and stall counters do not form a complete disjoint phase breakdown.
JOB_CYCLES includes DDR traffic through final B; UART wall time is separate.

This is a same-workload physical revision comparison, not an isolated READ_SLOTS experiment. DMA, burst handling, host control and build sources changed between images; a same-source READ1 board image is needed to isolate read-depth effects.

The explicit operating setting changes from READ_SLOTS=1 (implicit in the
older archive) to READ_SLOTS=4. Ten sealed build inputs differ:

- `platform/nexys_a7/gemm_ddr_top.sv` (changed)
- `rtl/control/gemm_ddr_control.sv` (changed)
- `rtl/control/gemm_ddr_core.sv` (changed)
- `rtl/memory/gemm_axi_burst.sv` (changed)
- `rtl/memory/gemm_dma_rows.sv` (changed)
- `rtl/memory/gemm_tile_dma.sv` (changed)
- `rtl/memory/gemm_tile_dma_read_queue.sv` (added)
- `scripts/build_ddr_gemm.py` (changed)
- `scripts/build_ddr_gemm.tcl` (changed)
- `tb/vendor/tb_ddr_gemm.sv` (changed)

The public host pack/client/protocol files remain byte-identical under the
recorded UTF-8/LF hash basis. The two programming/qualification runner source
hashes also changed and are enumerated in the JSON. Reconstructed descriptors,
guarded images and sentinel hashes exactly match the sealed historical
[workload identity](../../p8_scaling/comparison.json); these are not captured
input bus traces.

Runs were sequential by image after separate operator-confirmed cold cycles.
Dense rows are paired by ordinal index only for presentation, without an
interleaved experiment or fixed saving claim. [manifest.json](manifest.json)
binds both board manifests, their qualification archives and the derived files.
This bounded comparison does not qualify overlap, maximum shapes, warm-reset
recovery, endurance or lifetime reliability.
