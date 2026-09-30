# Project status

Updated 2026-09-30. Implementation target: [specification.md](specification.md),
with [design decisions](decisions.md).

| Area | Evidence | Remaining work |
| --- | --- | --- |
| Board baseline | September 30: xc7a50t detected/programmed, user-confirmed calibration LEDs, UART ping and 8 adjacent/7 scattered DDR words passed on COM11 | Physical revision/marking; platform reset/constraint review; repaired-source board build |
| Compute | P4/P8 core, independent numerical/cycle tests, detected mutations; standalone 100 MHz timing with 16/64 DSPs | Timing with local memory and board wrapper |
| BRAM preview | Local engine, framed UART, command controller, Python host and board top; complete portable regression passes | Complete board timing closure and hardware comparison |
| DDR system | Existing native MIG configuration is a reference | AXI MIG/conversion, DMA, scheduler, response/error handling |
| Full release | Technical contract specified | Overlap, concurrency, scoped formal checks, board endurance and controlled benchmarks |

The September 27 PE/P4/P8 regressions passed on unchanged compute RTL.
Core timing evidence is from September 14. The
[September 30 operand-memory results](../results/operand_memory/README.md)
add 662 simulated microtiles and synthesis checks for BRAM/DSP mapping.
Neither establishes full-system board timing. No GEMM board bitstream or
hardware GOPS is available.
The [local-engine results](../results/tile_engine/README.md) add 164 complete
jobs with 23,360 checked outputs. The routed-clock harness passes P4 and P8
at 100 MHz with 16 and 64 DSPs. Setup/hold margins are +0.948/+0.015 ns and
+0.422/+0.015 ns. This supersedes the estimated external-clock harness for
integration feasibility; the earlier failed reports remain preserved.
The preview controller adds 21 successful matrix jobs and 5,580 output
comparisons, including maximum dimensions and injected protocol faults.
The [September 30 board record](../results/native_board/2026-09-30.json)
and [raw output](../results/native_board/2026-09-30.txt) apply to the unchanged
July native bitstream, not the repaired sources or new GEMM memory wrapper.

## Immediate execution

1. Close full-board timing, including packet buffering and CRC logic.
2. Run full result comparisons on the FPGA and save the bitstream identity,
   configuration, test seed, cycle counts, timing, and utilization.

The BRAM preview is the next deliverable. DDR platform work must not block it.
Tests, simulation, synthesis, and board measurements are release checks.
