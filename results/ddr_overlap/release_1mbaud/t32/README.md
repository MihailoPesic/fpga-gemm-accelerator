# T32 1 Mbaud candidate: simulation and implementation

Vivado 2026.1 completed vendor simulation and a routed bitstream for the Nexys
A7-50T, image **`0x9d4beb4d`**, VERSION `0x200`, P8/T32 and four outstanding
reads. The production UART setting is **1,000,000 baud**. Both MODE=0 and MODE=1
remain selectable in the same image.

This record establishes simulation, implementation and a provisional static
review. **Physical 1 Mbaud operation and matrix results are not established by
this record.** The separately measured
[115200-baud image](../../timing_predicate/final_build/board/README.md) has a
different build identity. Its performance numbers are not measurements of this
candidate.

| Routed quantity | Result |
| --- | ---: |
| Worst setup slack | +0.017 ns |
| Worst hold slack | +0.017 ns |
| Bus-skew checks / worst slack | 10 / +9.019 ns |
| LUTs | 17,026 / 32,600 (52.23%) |
| Flip-flops | 20,882 / 65,200 (32.03%) |
| DSP48E1 | 75 / 120 (62.50%) |
| RAMB36 / RAMB18 | 16 / 8 |
| BRAM36 equivalents | 20 / 75 (26.67%) |
| Occupied slices | 7,001 / 8,150 (85.90%) |
| Unique control sets | 1,616 |

Setup, hold and pulse-width summaries have no failing endpoints; every routable
net is routed. The positive setup margin is only 17 ps. Slice occupancy and
control routing remain practical constraints despite unused arithmetic and
memory resources. The worst setup path runs from the burst reader's queued
response-head register to the job error-code register enable: 9.785 ns of data
delay, including 7.841 ns of routing and 12 logic levels. It is a control path,
rather than an array multiplier path.

The core runs at 100 MHz. SmartConnect bridges its 64-bit AXI interface to the
128-bit MIG interface at 50 MHz. The saved clock report contains 22 clock
definitions. The complete interaction table has 24 pairs: 20 Clean and four
Ignored, comprising three `Max Delay Datapath Only` relationships and one
`False Path` relationship. Vendor CDC and timing exceptions are retained.

The vendor simulation passed three framed-UART jobs: `(M,N,K)=(1,1,1)`,
`(5,3,9)` and `(1,33,9)`, comparing **49 outputs**. The third job crosses a
macrotile boundary in MODE=1. The final transcript records 118 packets,
126 read beats, 152 write beats and 66 write responses, including host memory
transfers. Simulation used **10 Mbaud**, FAST calibration, a cold-start
sequence and zero PCB delays. It does not test the physical 1 Mbaud link,
warm reset or board timing margins.

The own-checkpoint reset review exited zero and preserved its DCP hash. Its
result remains `PASS_PROVISIONAL_REVIEW`. The 86 asynchronous reset endpoints
match the previously reviewed selector: 85 endpoints have 170 false-path
checks, while the remaining MIG endpoint has timed slacks of +1.383 and
+16.474 ns. Of 101 vendor primitive reset/control pins, 46 appear in normal or
ignored timing reports; 55 have no such reported path, including 18 tied to
zero and 37 dynamic/unknown controls. These limits are retained, rather than
interpreting absent paths as proof of reset behavior.

The CDC summary has eight CDC-3 information checks, two CDC-8 warnings and
353 CDC-15 warnings. DRC retains 21 warnings; methodology retains 47 warnings
and 16 advisories. The classes match the previously reviewed design. Missing
I/O delays are limited to asynchronous `CPU_RESETN`, UART input, UART output
and four LEDs; UART and LED paths have explicit exceptions. These reports do
not establish warm-reset correctness. The original warning details and all
constraints remain available for review.

[build.json](build.json) and [vendor/simulation.json](vendor/simulation.json)
retain their original bytes, source commit and dirty state.
[build_stages.json](build_stages.json) preserves only the completed T32
simulation and bitstream subprocess receipts; [review_stages.json](review_stages.json)
preserves its completed review receipt. All three exits were zero. The parent
orchestration was still running T8 when these receipts were selected; no
overall parent/T8 completion is claimed here.

[manifest.json](manifest.json) seals the publication and maps compressed files
back to their original byte counts and hashes. All 13 routed reports and all
28 artifacts covered by the original
[static review record](routed/static/record.json) are preserved. Large logs,
timing reports and reset inventories use lossless deterministic gzip.
[metrics.json](metrics.json) summarizes the reports;
[sources.tar.gz](sources.tar.gz) and [source_inventory.json](source_inventory.json)
preserve the exact 32 build inputs. [source_verification.json](source_verification.json)
records their comparison with current sources and the actual bitstream/DCP.

The generated bitstream and DCP are not stored in this evidence directory.
Their checked SHA-256 identities are:

```text
bitstream d187f51bac37c3682641243f1db112579ad14c8ac45a1b3df52e704bf58c8327
DCP       92b67ec1910729af2824ec7221fd49a1f7101decb6561feaefd35a353e294fa7
```

Verify all retained artifacts, source snapshots, actual stage receipts and
simulation counters without launching Vivado or accessing hardware:

```sh
python results/ddr_overlap/release_1mbaud/t32/verify.py --check-current
```

Decompress and verify every original report and source snapshot into a fresh
workspace directory:

```sh
python results/ddr_overlap/release_1mbaud/t32/verify.py --extract-raw build/t32_1mbaud_reports_verified
```

Regenerate the candidate from the matching sources with Vivado installed:

```sh
python scripts/build_ddr_gemm.py --stage sim --p 8 --t 32 --read-slots 4 --overlap --baud 1000000 --build-dir build/t32_1mbaud_rebuild
python scripts/build_ddr_gemm.py --stage bitstream --p 8 --t 32 --read-slots 4 --overlap --baud 1000000 --build-dir build/t32_1mbaud_rebuild
```

Physical qualification requires the exact new image, an operator-confirmed
cold power cycle, a 1 Mbaud transport check and complete matrix/guard comparisons.
