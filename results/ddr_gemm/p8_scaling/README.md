# Physical array scaling: P4 to P8

The same seven guarded workloads ran on [P4](p4/README.md) and
[P8](../p8_serial/board/README.md), with 48 completed jobs per image.
All 28 RTL/platform/build source hashes and all six host/runner hashes match.
The array parameter changes from P4 to P8; T32, signed INT8/INT32 arithmetic,
100 MHz, serial scheduling, 115200 baud, matrix bytes, seeds and residency remain
the same. Each image passed every output/guard comparison without transport errors.

| Case | P4 cycles: min / median / max | P8 cycles: min / median / max | Median job speedup |
| --- | ---: | ---: | ---: |
| signed_scalar | 203 / 204 / 204 | 216 / 217 / 220 | 0.9401x |
| signed_extremes | 919 / 926 / 928 | 932 / 933 / 940 | 0.9925x |
| odd_tail | 835 / 836 / 843 | 823 / 824 / 828 | 1.0146x |
| column_tile_boundary | 13902 / 13915 / 13940 | 12493 / 12500 / 12518 | 1.1132x |
| multi_tile_kmax | 54958 / 54985 / 54992 | 40087 / 40101 / 40133 | 1.3712x |
| asymmetric_k255 | 153692 / 153768 / 153788 | 100608 / 100653 / 100709 | 1.5277x |
| resident_dense | 34025 / 34103.0 / 34241 | 21195 / 21281.5 / 21366 | 1.6025x |

For the 32 x 32 x 256 dense case, the 30-run median changes from
34103.0 to 21281.5 cycles:
1.602472x measured job speedup. Median useful throughput is
1.537366 versus 2.463586 GOPS.
Active compute falls from 17,088 to 4,464 cycles. Each run still accepts
2,048 read beats and 512 write beats with 4,096 useful write bytes.
Job timing includes DDR work and the final write response; arithmetic peak
and active-compute timing are not substituted for this measurement.

[comparison.json](comparison.json) binds both board manifests and raw result
hashes, all seven distributions and the 30 dense observations from each image.
[comparison.csv](comparison.csv) contains matching summary and repetition rows.
The saved counters include active compute, input waits and AXI data stalls;
these are not an exhaustive disjoint phase breakdown. Descriptor/image hashes
are reconstructed from the sealed host packing code, not captured bus traces.

Runs were sequential by image, with a fresh cold cycle before P8. Dense records
are paired by repetition index for presentation, not interleaved experiments.
Actual DDR timing variation remains visible; no fixed cycle saving is assumed.
The result applies to these workloads and this serial architecture, without
claiming overlap, concurrent reads, P8 endurance or warm-reset recovery.
