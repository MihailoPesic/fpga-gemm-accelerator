# Project status

Updated 2026-09-30. Implementation target: [specification.md](specification.md),
with [design decisions](decisions.md).

| Area | Evidence | Remaining work |
| --- | --- | --- |
| Board baseline | September 30: xc7a50t detected/programmed, user-confirmed calibration LEDs, UART ping and 8 adjacent/7 scattered DDR words passed on COM11 | Physical revision/marking; platform reset/constraint review; repaired-source board build |
| Compute | P4/P8 core, independent numerical/cycle tests, detected mutations; routed local-engine timing at 100 MHz with 16/64 DSPs | P8 full-board integration |
| BRAM preview | P4/T32 board bitstream passes 100 MHz timing and full output comparisons over UART; host, transport, controller and serial-pin regressions pass | Longer board workload, faster UART validation if needed |
| DDR system | Existing native MIG configuration is a reference | AXI MIG/conversion, DMA, scheduler, response/error handling |
| Full release | Technical contract specified | Overlap, concurrency, scoped formal checks, board endurance and controlled benchmarks |

The September 27 PE/P4/P8 regressions passed on unchanged compute RTL.
Core timing evidence is from September 14. The
[September 30 operand-memory results](../results/operand_memory/README.md)
add 662 simulated microtiles and synthesis checks for BRAM/DSP mapping.
These earlier core/memory results do not establish full-system board timing.
The [local-engine results](../results/tile_engine/README.md) add 164 complete
jobs with 23,360 checked outputs. The routed-clock harness passes P4 and P8
at 100 MHz with 16 and 64 DSPs. Setup/hold margins are +0.948/+0.015 ns and
+0.422/+0.015 ns. This supersedes the estimated external-clock harness for
integration feasibility; the earlier failed reports remain preserved.
The preview controller adds 21 successful matrix jobs and 5,580 output
comparisons, including maximum dimensions and injected protocol faults.
The complete [BRAM preview](../results/preview/README.md) now passes routed
setup/hold at +0.188/+0.005 ns and runs on the physical Nexys A7-50T. All six
hardware cases pass over 30 repetitions each: 180 jobs and 90,510 checked
outputs, including odd/non-square shapes and maximum signed extrema. There
were no mismatches, UART retries or rejected frames. The 32x32x256 job takes
17,344 cycles at 100 MHz: 173.44 us and
3.02 useful GOPS for the BRAM-local job. UART transfers are excluded from
that counter and recorded separately. DDR-backed performance remains unmeasured.
The [September 30 board record](../results/native_board/2026-09-30.json)
and [raw output](../results/native_board/2026-09-30.txt) apply to the unchanged
July native bitstream, not the repaired sources or new GEMM memory wrapper.

## Immediate execution

1. Establish a separate AXI MIG platform build and run its vendor memory test.
   Confirm the generated user clock/width, reset sequencing and constraints.
2. Implement and test serial AXI DMA against a behavioral RAM, including
   independent channel stalls, response errors, burst splits and tail strobes.
3. Connect the verified local engine to DDR and compare complete board outputs
   before adding overlap or concurrent reads.

The working BRAM preview remains the reproducible board checkpoint while the
DDR path is developed. It is a smaller release with its own identity and
limits, not compliance with the full DDR-backed contract.
