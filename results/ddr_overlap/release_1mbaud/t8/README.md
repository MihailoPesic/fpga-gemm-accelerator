# T8 1 Mbaud candidate: simulation and implementation

Vivado 2026.1 completed vendor simulation and a routed bitstream for the Nexys
A7-50T, image **`0xeed7b111`**, VERSION `0x200`, P8/T8 and four outstanding
reads. The production UART setting is **1,000,000 baud**. Both MODE=0 and MODE=1
remain selectable in the same image.

This record establishes simulation, implementation and a provisional static
review. **Physical 1 Mbaud operation and matrix results are not established by
this record.** The measured
[115200-baud image](../../timing_predicate/final_build/board/README.md) has a
different identity; its performance numbers do not describe this candidate.

| Routed quantity | Result |
| --- | ---: |
| Worst setup slack | +0.117 ns |
| Worst hold slack | +0.015 ns |
| Bus-skew checks / worst slack | 10 / +8.996 ns |
| LUTs | 16,956 / 32,600 (52.01%) |
| Flip-flops | 20,836 / 65,200 (31.96%) |
| DSP48E1 | 75 / 120 (62.50%) |
| RAMB36 / RAMB18 | 16 / 8 |
| BRAM36 equivalents | 20 / 75 (26.67%) |
| Occupied slices | 6,787 / 8,150 (83.28%) |
| Unique control sets | 1,654 |

Setup, hold and pulse-width summaries have no failing endpoints; every routable
net is routed. The worst setup path runs from the captured N dimension to the
job error-code register enable. Its data delay is 9.478 ns, including 7.077 ns
of routing and 11 logic levels. It is a descriptor-control path. Positive
timing slack remains small, and slice occupancy is a practical constraint.

T8 and the [T32 candidate](../t32/README.md) use the same P, clock, precision and
read depth, providing the intended builds for the reuse comparison. No physical
reuse speedup is measured here. The T8 build retains **20 BRAM36 equivalents**,
despite its smaller logical buffers. Vivado adds eight `SYNTH-4` warnings for
shallow result BRAMs and suggests distributed RAM. The bank implementation is
unchanged; the complete warning details are retained.

The core runs at 100 MHz. SmartConnect bridges its 64-bit AXI interface to the
128-bit MIG interface at 50 MHz. The clock report has 22 definitions. The full
interaction table has 24 pairs: 20 Clean and four Ignored, comprising three
`Max Delay Datapath Only` relationships and one `False Path` relationship.
Vendor CDC and timing exceptions are retained.

Vendor simulation passed three framed-UART jobs: `(M,N,K)=(1,1,1)`, `(5,3,9)`
and `(1,9,9)`, comparing **25 outputs**. The third job crosses a macrotile
boundary in MODE=1. The final transcript records 94 packets, 66 read beats,
80 write beats and 40 write responses, including host memory transfers.
Simulation used **10 Mbaud**, FAST calibration, a cold-start sequence and zero
PCB delays. It does not test the physical 1 Mbaud link or warm reset.

The own-checkpoint reset review exited zero and preserved its DCP hash. Its
result remains `PASS_PROVISIONAL_REVIEW`. Of 86 asynchronous reset endpoints,
85 have 170 false-path checks; the remaining MIG endpoint has timed slacks of
+2.482 and +14.203 ns. Of 101 vendor primitive reset/control pins, 46 appear in
normal or ignored timing reports. The other 55 include 18 tied to zero and
37 dynamic/unknown controls. Absent paths do not establish reset behavior.

The CDC report has eight CDC-3 information checks, two CDC-8 warnings and
353 CDC-15 warnings. DRC retains 21 warnings; methodology reports 55 warnings
and 16 advisories, including the eight additional shallow-BRAM warnings.
Missing I/O delays are limited to asynchronous `CPU_RESETN`, UART input, UART
output and four LEDs. UART and LED paths have explicit exceptions. These
reports do not qualify warm reset.

[build.json](build.json) and [vendor/simulation.json](vendor/simulation.json)
retain their original bytes, source commit and dirty state.
[build_stages.json](build_stages.json) preserves the completed T8 simulation
and bitstream subprocess receipts; [review_stages.json](review_stages.json)
preserves its completed static-review receipt. All three actual exits were zero.

[manifest.json](manifest.json) seals the publication and maps compressed files
to their original byte counts and hashes. All 13 routed reports and all
28 artifacts sealed by the original [static review](routed/static/record.json)
are preserved. Large logs and reports use lossless deterministic gzip.
[metrics.json](metrics.json) summarizes the reports;
[sources.tar.gz](sources.tar.gz) and [source_inventory.json](source_inventory.json)
preserve the exact 32 build inputs. [source_verification.json](source_verification.json)
records their comparison with current sources and the actual image/checkpoint.

The generated bitstream and DCP are not stored in this evidence directory.
Their checked SHA-256 identities are:

```text
bitstream 21845beeb677bc8c0445231646cefb6e3633bdcf58a2d8c0566bcceaf44d6f37
DCP       893acae0f3c1a7c41f52e030dee70235b5b4630187c9614c1161f93fbb4d4ae9
```

Verify all retained artifacts, source snapshots, stage receipts and simulation
counters without launching Vivado or accessing hardware:

```sh
python results/ddr_overlap/release_1mbaud/t8/verify.py --check-current
```

Decompress and verify every original report and source snapshot into a fresh
workspace directory:

```sh
python results/ddr_overlap/release_1mbaud/t8/verify.py --extract-raw build/t8_1mbaud_reports_verified
```

Regenerate the candidate from the matching sources with Vivado installed:

```sh
python scripts/build_ddr_gemm.py --stage sim --p 8 --t 8 --read-slots 4 --overlap --baud 1000000 --build-dir build/t8_1mbaud_rebuild
python scripts/build_ddr_gemm.py --stage bitstream --p 8 --t 8 --read-slots 4 --overlap --baud 1000000 --build-dir build/t8_1mbaud_rebuild
```

Physical qualification requires the exact new image, an operator-confirmed
cold power cycle, a 1 Mbaud transport check and complete matrix/guard comparisons.
