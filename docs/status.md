# Project status

Updated 2026-10-09. The measured development build is P8/T32, READ4,
100 MHz, 1 Mbaud, VERSION `0x200`, image `0x9d4beb4d`. It performs complete
DDR2-backed matrix jobs on the Nexys A7-50T in serial and overlap modes.
Documentation and command cleanup do not change that hardware image.

## Implemented

- Signed INT8 multiply with INT32 accumulation; M,N=1..1024 and K=1..256.
- Output-stationary array, wide banked local memory, word prefetch and exact tails.
- Burst splitting, independent AXI handshakes, four read slots and response checks.
- Two operand sets and two result sets with tagged load/compute/store ownership.
- Descriptor validation, watchdog, first fatal error and outstanding-transaction drain.
- Framed UART commands with CRC, duplicate-request replay and idle host-memory access.
- Python packing, job control, full output/guard validation and separate host timings.

The [architecture](architecture.md), [register interface](ddr-registers.md)
and [overlap contract](ddr-overlap.md) describe the implemented behavior.

## Measured evidence

| Check | Result |
| --- | --- |
| Dense 256x256x256 | 30 matched pairs; overlap median 297,001 cycles / 11.298 useful GOPS; serial 741,063.5 cycles; paired speedup 2.495x |
| Controlled T8/T32 comparison | 16 shapes, 30 samples per series; 2.293x from T8 to T32 serial, another 2.495x from overlap; input traffic falls 4x |
| T32 shape grid | 960 jobs, 15,981,960 outputs and complete guarded allocations checked |
| Maximum dimensions | One 1024x1024x256 job per mode; 2,097,152 outputs independently replayed from retained memory bytes |
| Repeatability | 592 mixed jobs, 550,634 outputs over 30.06 host-paced minutes; zero mismatches or UART retries |
| CLI/API smoke | Fresh cold start; supplied matrices in both modes plus three odd-shape CLI jobs; all 53 outputs and memory guards checked |
| Implementation | Setup/hold +0.017/+0.017 ns; 17,026 LUTs, 20,882 FFs, 75 DSPs, 20 RAMB36 equivalents |

[Measurements](measurements.md) explains the controlled result and its scope.
The [current evidence index](../results/ddr_overlap/release_1mbaud/README.md)
links each original record, source/image identity and validator. Portable
tests, vendor simulation, formal checks and board measurements have distinct
boundaries in the [verification overview](verification.md).

The first T32 endurance attempt stopped after 381 validated jobs around a
recorded Windows Modern Standby interval. A read-only reconnect checked the
pending job and guards; it did not turn that failed run into a pass. The
replacement run starts from zero elapsed time. Both records remain in the
[evidence index](../results/ddr_overlap/release_1mbaud/README.md).

<a id="next-gates"></a>

## Open limits

| Area | Current boundary |
| --- | --- |
| DDR reset | Board use requires fresh OFF/ON power-up with JP1 in JTAG mode before programming. Warm-reset clock/CKE timing remains unqualified. |
| Physical identification | Nexys A7 / 50T / CSG324 and ISSI IS43DR16640C were reported; complete FPGA/DDR suffixes and PCB revision remain unrecorded. |
| Timing margin | Routed timing passes at 100 MHz, with only 17 ps worst setup margin and 85.9% occupied slices. CDC/reset findings and vendor exceptions remain recorded. |
| Performance scope | DDR-resident job throughput is measured. Independent resident-array throughput and isolated DDR read-only/write-only/mixed bandwidth are not yet measured. |
| Memory qualification | Board jobs and bounded diagnostics pass; they are not a full-memory/electrical-margin qualification. |
| Formal scope | Queue induction and bounded/reduced scheduler/DMA checks do not prove the complete accelerator. |

The configured Micron MIG preset and reported ISSI chip are reviewed in
[memory compatibility](ddr-memory-compatibility.md). Correct output data alone
does not qualify reset sequencing or physical timing margin.

## Original v1 target

The [September v1 specification](specification.md) is the original target,
not the implemented interface. The development image advertises VERSION
`0x200`; the proposal uses `0x00010000`. Its measurement and platform gates
also include the unresolved items above. Full-v1 compliance is not claimed.

Earlier BRAM, native-DDR, serial and 115200-baud overlap builds retain their
own evidence in [history](history.md). They do not qualify a changed image.
Generated projects, reports, environments and bitstreams stay in ignored
`build/`; a clean clone regenerates its image through the
[board build procedure](ddr-board.md).
