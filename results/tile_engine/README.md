# Local-engine verification - 2026-09-30

Simulation passes for the complete local matrix engine and its result banks.
The integrated engine has not passed routed timing or run on the board.

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
identifies the source files and saved evidence by SHA-256 of UTF-8/LF content.
These tests do not cover UART, DDR DMA, or the future overlapping scheduler.

## Open timing issue

Vivado 2026.1, `xc7a50ticsg324-1L`, P4,T32, registered-neighbor harness:

| Metric | Routed result |
| --- | ---: |
| Target clock / uncertainty | 100 MHz / 0.200 ns |
| DSP48E1 | 16 |
| RAMB36E1 / RAMB18E1 | 8 / 4 |
| LUTs / fabric registers, including harness | 1,342 / 1,882 |
| Setup slack | +0.642 ns |
| Hold slack | -0.036 ns: FAIL |
| Unconstrained internal endpoints | 0 |

The worst hold path uses dedicated DSP cascade routing from PE(1,2) to
PE(1,3), ending at ACIN. `phys_opt_design -hold_fix` followed by routing did
not remove the violation. Vivado warns that dedicated routing prevents the
router from adding a delay detour. The next timing investigation must address
DSP mapping/placement or clock skew; the failed path is not waived.

The worst setup path runs from the microtile elapsed counter to a DSP B input,
with 7.433 ns data delay, including 5.072 ns routing. Methodology reports 12
SYNTH-6 warnings concerning BRAM output-register use and no critical warnings.
External harness pins are deliberately excluded; all engine and neighboring
register paths are timed. These are standalone integration results, not board
timing constraints or a hardware performance measurement.

The saved P4 reports record a single-worker run with automatic hold repair.
An earlier route found the same failing path. Intermediate run logs and the
redundant snapshot remain local build artifacts. The script fails at P4, so
no integrated P8 routing result is claimed.

Reproduce simulation with `make test-tile` and routing with `make synth-tile`.
The latter currently returns failure at the hold check.
