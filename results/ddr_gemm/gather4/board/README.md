# C-gather optimization: board qualification

The P4/T32 serial DDR GEMM image `0x4898db67` passed the unchanged seven-case,
48-job hardware qualification: **49,593 outputs** matched the wide-integer oracle,
and **689,628 input/padding/guard bytes** were checked. UART recorded no retries,
rejected frames or poisoned transport state. Complete elapsed time was
220.744757300 seconds, including host transfers, comparisons and persistence.

The board ran at 100 MHz with 115200-baud UART after a new operator-confirmed
cold power cycle and volatile JTAG programming. [The cold-start record](cold_start_confirmation.json)
and [program log](program.log) identify this image. Bitstream SHA-256:
`55eafc4dba5053b12d8299165b6d2deddb2dea9e7fb76d4ec6b4b4403cf1b910`. The as-built source state was dirty;
[inputs.json](inputs.json) preserves its 28 input hashes and [build.json](build.json)
preserves the original simulation/routed qualification.

| Workload | Runs per image | Original cycles min/median/max | New cycles min/median/max | Median cycles saved |
| --- | ---: | ---: | ---: | ---: |
| signed_scalar | 3 | 205/206/206 | 203/203/210 | 3 |
| signed_extremes | 3 | 919/923/924 | 920/923/951 | 0 |
| odd_tail | 3 | 848/852/865 | 838/839/842 | 13 |
| column_tile_boundary | 3 | 14419/14446/14462 | 13905/13926/13971 | 520 |
| multi_tile_kmax | 3 | 55601/55610/55653 | 54949/54982/55022 | 628 |
| asymmetric_k255 | 3 | 155879/155914/155958 | 153697/153807/153848 | 2107 |
| resident_dense | 30 | 34577/34637.0/34673 | 34027/34082.0/34175 | 555.0 |

The [original board measurements](../../board/README.md) used image `0xed44f92e`.
Both datasets use identical P4/T32 geometry, clock, signed INT8/INT32 arithmetic,
workload shapes, seeds, input residency, complete comparisons, guard checks and
host source hashes. The only changed build-input source is `gemm_tile_dma.sv`.
Placement and DDR timing can differ between physical runs, so the table reports
actual distributions rather than assuming a fixed cycle saving.

[comparison.json](comparison.json) binds both images and the exact old/new raw
hashes, includes all seven case distributions, and retains the 30 dense repetitions
from each image. [comparison.csv](comparison.csv) contains seven case-summary rows
and 30 ordinally paired dense rows. Those pairs are a presentation by repetition
index, not interleaved or simultaneous measurements.

JOB_CYCLES covers validated START through the final successful result B response.
Each case uploads once, then reuses DDR inputs for its remaining repetitions;
every repetition still downloads and checks all outputs and allocation guards.
Host-inclusive time excludes validation and guard checking; the complete elapsed
time above includes them. The raw records retain each separate timing field.

This qualifies the fixed 48-job suite on this image. It does not extend the old
image's maximum-shape result to this image, establish 30-minute repeatability,
test the complete DDR address space, or qualify warm reset or P8.

```text
python scripts/hw_test_ddr_gemm.py --port COMx --manifest build/gemm_gather4/build.json --output <fresh-directory> --retries 0
```

[results.json](results.json), [results.csv](results.csv), [summary.json](summary.json),
and the seven case directories are byte-for-byte original records.
[manifest.json](manifest.json) records their hashes, the raw startup/build seals,
and separate hashes for the derived comparison files. Historical manifest fields
remain unchanged.
