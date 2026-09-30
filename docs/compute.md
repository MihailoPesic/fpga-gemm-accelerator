# Compute interface

`gemm_microtile` calculates one output-stationary P x P tile, with P=4 or 8,
1 <= K <= 256, and independent valid row/column counts in 1..P. It implements
F-01, F-04 and the compute portion of D-01/D-02. It contains the PE mesh,
operand skew and result selection. Operand BRAMs, prefetch, C banks, macrotile
iteration, descriptors and memory transactions are outside this module.
The implemented banks and scheduler are described in [tile-engine.md](tile-engine.md).

```
prepared A/BT vectors -> row/column skew -> registered PE mesh -> row drain
                              ^                  ^                 |
                              +--- fixed schedule control --------+
```

## Rising-edge convention

| Edge | Event |
|---|---|
| 0 | Accepted start snapshots K/rows/cols, clears every accumulator and validity pipeline |
| 1+k | Input vector k is sampled; lane 0 enters PE(0,0) |
| 1+k+r+c | A[r,k] and BT[c,k] are sampled by PE(r,c) |
| 2+k+r+c | That product commits to C[r,c] |
| K+2P-1 | Last product commits anywhere in the array |
| K+2P through K+3P-1 | Destination captures rows 0 through P-1 |
| K+3P-1 | Last drain is captured, busy falls, done pulses |

For P=4,K=1: launch 0; first multiply 1; first commit 2; last commit 8;
drain edges 9,10,11,12. For P=8,K=256: last commit 271; drain 272..279.
An immediate next launch can be accepted at edge K+3P. Real prefetch/control
may add cycles; see [memory.md](memory.md) for the implemented prefetch schedule.

## Interface responsibilities

`start_ready` is high while idle and outside reset. Pulse `start` with legal
dimensions for one cycle. `cmd_error` pulses for illegal dimensions or a start
while busy; neither changes the running operation. Configuration is snapshotted
at acceptance. `done` is a one-cycle pulse, not the later sticky job status.

When `feed_valid` is high, supply vector `feed_index` before the next rising
edge. Pack A[q,k] and BT[q,k] into lane q, bits [8*q +: 8]. All K vectors arrive
on consecutive cycles. The core masks unused rows/columns and stops consuming
at K; unused bytes may be arbitrary. The prefetcher must prepare words
before launch and keep this schedule through synchronous BRAM reads.

On each rising edge with `drain_valid`, capture `drain_data` and `drain_mask`
at row `drain_row`. Each lane contains one signed 32-bit result. The mask is
zero for padded rows/columns, and masked data is zero. All P rows are scheduled,
including masked rows. The destination must reserve space before launch;
there is no ready signal and no supported pause inside a microtile.

Synchronous active-high `rst` abandons the local operation and clears valid
state. This is only a core reset contract: a future AXI system must coordinate
platform reset and outstanding transaction obligations separately.

## Arithmetic and failure modes

Both multiplication operands and the 32-bit registered product are signed.
SystemVerilog assignment sizing sign-extends the product before accumulation.
The wide product register is intentional: synthesis trials found that a narrow
product plus explicit concatenation could cost two DSPs per PE. Synthesis must
still establish one DSP per PE for these production modules.

Tests must catch wrong signedness (especially -128), stale products after
clear, incorrect row/column skew, mismatched reduction indices, early drain,
tail leakage, K=256 counter wrap, repeated start, reset during work and stale
state between launches. Simulation-only k tags travel with operands through
the skew and mesh and assert that every valid pair has equal reduction indices.
The numerical oracle directly multiplies Python integers, independently of
the RTL schedule.
