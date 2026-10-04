# P8 measurements at 1 Mbaud

The T32 image `0x9d4beb4d` and T8 image `0xeed7b111` use the same portable
sources, 100 MHz core, signed INT8/INT32 arithmetic and four read slots.
Both provide selectable MODE0/1 and development VERSION `0x200`. T is the
changed build setting; the images have separate timing and hardware identities.

| Record | Completed scope |
| --- | --- |
| [T32 implementation](t32/README.md) | Vendor simulation, routed bitstream and own-checkpoint static review; setup/hold +0.017/+0.017 ns |
| [T8 implementation](t8/README.md) | Vendor simulation, routed bitstream and own-checkpoint static review; setup/hold +0.117/+0.015 ns |
| [T32 dense board comparison](board/t32/dense/README.md) | 30 matched MODE0/1 pairs at 256x256x256; 3,932,160 output comparisons; median 11.297751 useful GOPS with overlap and 2.495140x paired speedup |
| [T32 maximum-size check](board/t32/maximum/README.md) | One 1024x1024x256 job per mode; all 2,097,152 outputs and complete allocation snapshots independently replayed; overlap 11.509020 useful GOPS |
| [T32 complete benchmark grid](board/t32/benchmark/README.md) | 960 jobs across 16 shapes, 30 samples per mode; 15,981,960 outputs and full guarded allocations checked |
| [T32 fresh sustained run](board/t32/endurance/README.md) | 592 mixed jobs and 550,634 outputs over 30.06 continuous host-paced minutes; both modes, zero retries |
| [Stopped T32 endurance run and read-only recovery](board/t32/endurance_failed/README.md) | Original run failed after 381 validated jobs; host Modern Standby recorded; retained job's 64 outputs later checked separately without reset or START replay |

The dense measurement includes DDR tile transfers and final result-write
acknowledgement. Inputs stay in DDR between repetitions. Host packing,
USB-UART transfers and validation are recorded separately. It establishes
physical operation at 1 Mbaud for this T32 workload. The maximum-size check
adds complete comparisons in both modes, with one sample per mode. These
records are supplemented by the complete T32 shape grid and a separate fresh
30-minute mixed run. The T8 reuse baseline remains pending. Grid and endurance
archives replay counters, metadata and provenance; raw per-job matrices and UART
frames were not retained for independent numerical replay.

Each child archive retains its own original bytes, source snapshots, execution
receipts and standalone validator. A static archive's pre-board scope remains
unchanged when later board measurements are added. Generated bitstreams and
Vivado projects remain in ignored local build directories; their hashes are
recorded in the evidence.

Warm-reset DDR timing remains unqualified. All board evidence here uses a
reported cold power cycle with JP1 in JTAG mode. These development images do
not advertise the full v1 contract in [the specification](../../../docs/specification.md).
