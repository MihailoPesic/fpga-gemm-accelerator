# Local-engine verification - 2026-09-30

Simulation passes for the complete local matrix engine and its result banks.
P4 and P8 also pass 100 MHz timing in the standalone harness with a routed
global clock. These results do not establish timing or operation of a board top.

## Functional results

`make lint test mutation PYTHON=.venv/bin/python` completed under Ubuntu/WSL
with Icarus 12.0 and cocotb 2.0.1. The core and operand-memory regressions passed;
all three injected core defects were detected. The new local-engine tests add:

| Array / tile | Completed jobs | Checked output values | Completed microtiles |
| --- | ---: | ---: | ---: |
| P4 / T8 | 41 | 745 | 88 |
| P4 / T32 | 41 | 10,312 | 757 |
| P8 / T8 | 41 | 967 | 41 |
| P8 / T32 | 41 | 11,336 | 248 |
| Total | 164 | 23,360 | 1,134 |

Each configuration includes 25 seeded random jobs, directed tail/non-square
cases and maximum reduction length. Tests check every output with Python
integer dot products, every C write address, launch order, exact counters,
invalid/busy commands, stalled read responses and eight interrupted/reset
cases per configuration. Input padding is nonzero. Separate result-bank tests
enumerate all addresses and apply 200 random masked writes per configuration.

The coverage JSON files retain seeds and shapes. The eight XML files record
the result-bank and engine suite outcomes. [manifest.json](manifest.json)
identifies the historical source files and saved evidence by SHA-256 of UTF-8/LF
content. Its timing section describes the earlier failing harness below;
the passing clocked runs have a [separate manifest](clocked/manifest.json).
These tests do not cover UART, DDR DMA, or the future overlapping scheduler.

## Routed global-clock harness

Vivado 2026.1, `xc7a50ticsg324-1L`, T32, registered input/output neighbors,
10 ns clock period and 0.200 ns user uncertainty:

| Metric | P4 | P8 |
| --- | ---: | ---: |
| DSP48E1 | 16 | 64 |
| RAMB36E1 / RAMB18E1 | 8 / 4 | 16 / 8 |
| LUTs, including harness | 1,340 | 2,361 |
| Fabric registers, including harness | 1,882 | 3,587 |
| BUFG | 1 | 1 |
| Setup slack | +0.948 ns | +0.422 ns |
| Hold slack | +0.015 ns | +0.015 ns |
| Unconstrained internal endpoints | 0 | 0 |
| Routing errors | 0 | 0 |

[P4 reports](clocked/p4/summary.txt) and [P8 reports](clocked/p8/summary.txt)
include timing, utilization, clock routing, methodology and constraint checks.
The clock report identifies the actual BUFG at `BUFGCTRL_X0Y0`. The harness
excludes 114 external input paths and 242 external output paths; engine and
neighboring register paths are timed. UART, board I/O, platform reset and DDR
are outside this boundary.

P4's worst setup path runs from the reduction-length register to a PE DSP
input: 7.131 ns data delay, including 4.877 ns routing. P8's worst path runs
from the schedule counter to an operand-word register's clock enable:
9.102 ns, including 5.544 ns routing. Methodology reports only SYNTH-6 warnings
about unused BRAM output registers: 12 for P4 and 24 for P8. Neither run has
a methodology critical warning. The narrow hold margins remain part of the
reported result; further integration must pass its own timing checks.

## Earlier clock model

The original reports in this directory remain unchanged. They belong to
[commit e915a57](https://github.com/MihailoPesic/nexys-accelerator/commit/e915a57243f32e79aa8aaf5667d1e2762e738597),
whose harness used `HD.CLK_SRC` to estimate an external clock network.
That P4 run reported +0.642 ns setup and -0.036 ns hold slack on a dedicated
DSP cascade path; automatic hold repair did not remove the failure. P8 was
not run because the script stopped at P4.

The current harness instantiates and routes a BUFG, allowing timing analysis
to use an implemented global clock network. This corrects the physical
context of the standalone check. The compute, memory and engine RTL are
unchanged; this was not an arithmetic fix, a removed timing check or a board
performance improvement. The two sets of reports document different clock
models and should be read with their respective source hashes.

Reproduce simulation with `make test-tile` and routing with `make synth-tile`.
Routing launches one Vivado process per geometry; reports are written to
`build/synth_tile/p4` and `build/synth_tile/p8`.
