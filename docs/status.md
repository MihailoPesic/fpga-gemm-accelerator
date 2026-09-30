# Project status

Updated 2026-09-30. Implementation target: [specification.md](specification.md),
with [design decisions](decisions.md).

| Area | Evidence | Remaining work |
| --- | --- | --- |
| Board baseline | September 30: xc7a50t detected/programmed, user-confirmed calibration LEDs, UART ping and 8 adjacent/7 scattered DDR words passed on COM11 | Physical revision/marking; platform reset/constraint review; repaired-source board build |
| Compute | P4/P8 core, independent numerical/cycle tests, detected mutations; standalone 100 MHz timing with 16/64 DSPs | Timing with local memory and board wrapper |
| BRAM preview | Operand/result banks, microtile scheduling, counters and backpressured readback; 164 complete local jobs passed across P4/P8 and T8/T32 | Routed timing closure, UART commands, host application, board top and hardware comparison |
| DDR system | Existing native MIG configuration is a reference | AXI MIG/conversion, DMA, scheduler, response/error handling |
| Full release | Technical contract specified | Overlap, concurrency, scoped formal checks, board endurance and controlled benchmarks |

The September 27 PE/P4/P8 regressions passed on unchanged compute RTL.
Core timing evidence is from September 14. The
[September 30 operand-memory results](../results/operand_memory/README.md)
add 662 simulated microtiles and synthesis checks for BRAM/DSP mapping.
Neither establishes full-system board timing. No GEMM board bitstream or
hardware GOPS is available.
The [local-engine results](../results/tile_engine/README.md) add 164 complete
jobs with 23,360 checked outputs. The P4,T32 registered harness uses 16 DSPs
and 10 RAMB36 equivalents; setup slack is +0.642 ns, but hold slack is
-0.036 ns. Automatic hold repair did not close that dedicated DSP cascade
path. The timing script stops at P4, so the integrated P8 route is still pending.
The [September 30 board record](../results/native_board/2026-09-30.json)
and [raw output](../results/native_board/2026-09-30.txt) apply to the unchanged
July native bitstream, not the repaired sources or new GEMM memory wrapper.

## Immediate execution

1. Resolve the local engine's routed timing checks.
2. Add the UART command layer, host runner and preview board top; synthesize and route.
3. Run full result comparisons on the FPGA and save the bitstream identity,
   configuration, test seed, cycle counts, timing, and utilization.

The BRAM preview is the next deliverable. DDR platform work must not block it.
Tests, simulation, synthesis, and board measurements are release checks.
