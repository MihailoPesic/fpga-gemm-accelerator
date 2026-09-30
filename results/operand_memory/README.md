# Operand-memory integration, 2026-09-30

All four banked-memory/compute configurations passed simulation. The tested
sources and report hashes are recorded in [manifest.json](manifest.json).
This is an intermediate integration result, not a GEMM board release.

| P | T | Completed microtiles | LUTs | FFs | RAMB36 | DSP48 |
| --- | --- | --- | --- | --- | --- | --- |
| 4 | 8 | 107 | 1123 | 1230 | 8 | 16 |
| 4 | 32 | 227 | 1123 | 1234 | 8 | 16 |
| 8 | 8 | 149 | 2380 | 2940 | 16 | 64 |
| 8 | 32 | 179 | 2372 | 2942 | 16 | 64 |

Vivado 2026.1 synthesized `gemm_bram_microtile` out of context for
`xc7a50ticsg324-1L`. Counts include the operand buffers, prefetch and compute
core. No result storage, host transport, DMA, board wrapper or MIG is included.
No routing or timing claim is made. The expected "No cells matched RAMB18E1"
query warning means storage used RAMB36 primitives instead; synthesis reported
no RTL warnings, critical warnings or errors.

The 64-bit port width makes shallow T8 banks consume the same number of RAMB36
blocks as T32 banks. T8 reduces logical capacity but does not reduce BRAM count
in these builds. This is a measured synthesis mapping, not the logical bit count.

The simulation checks 662 completed microtiles in total, plus resets that
abandon work. It covers both buffers, every group pairing, all word addresses,
K boundaries, every valid row/column tail combination, busy/invalid starts,
same-buffer write blocking and concurrent writes to the other buffer.
Each build includes 50 seeded random jobs. Results are checked against Python
integer dot products; exact completion/drain timing is also checked.

Reproduce using `make test-memory PYTHON=.venv/bin/python` and
`vivado -mode batch -notrace -source scripts/synth_memory.tcl`.
