# Compute verification — 2026-09-14

The first production compute block implements the signed PE, P4/P8 skewed
array and fixed-schedule microtile drain. It is shared RTL for the future BRAM
preview and DDR-backed GEMM. It does not include banked RAM, prefetch, a
macrotile scheduler, DMA, register file, host transport or a board wrapper.

## Functional verification

| Test | Observed result |
|---|---|
| PE | All 65,536 signed INT8 operand pairs; 256-term positive/negative extrema and zeros/alternating signs; 2,000 valid-bubble/reset cycles; in-flight clear |
| P4 | 544 complete jobs, including 500 seeded random jobs; all 16 row/column tail combinations; five interrupted/reset jobs followed by correct fresh jobs |
| P8 | 592 complete jobs, including 500 seeded random jobs; all 64 tail combinations; five interrupted/reset jobs followed by correct fresh jobs |
| Both arrays | K=1,2,7,8,9,31,32,33,255,256; non-square/isolated-nonzero cases, arbitrary invalid-lane input data, exact feed/drain/DONE edges, busy/invalid starts, configuration snapshot and immediate next launch |
| Defect injection | Unsigned multiply, product surviving clear, and one-cycle-early drain each caused a running test to fail |
| Compile check | Icarus `-g2012 -Wall`, P4 and P8, passed |

Every useful output is compared with direct Python integer dot products.
Simulation tags travel with operands and assert matching reduction indices
inside each PE. This is not a formal proof, exhaustive job verification, or
verification of the not-yet-written buffer ownership and AXI behavior.

The environment was Icarus 12.0, cocotb 2.0.1 and find_libpython 0.5.1 in
Ubuntu/WSL. The runner reports Python 3.12.3; cocotb's embedding log reports
3.12.4 from the library found on this machine. Seeds are explicitly set in
the test code (20260914 for the PE; 20260914+P for each array).

[tests.json](tests.json), P4/P8 coverage files, result XML and mutation logs
preserve the outcomes. [manifest.json](manifest.json) records source and
evidence hashes. The recorded base commit is the preceding repository version;
source hashes identify the tested RTL, which was uncommitted at capture time.

## Routed standalone core

Vivado 2026.1 build 6511674, xc7a50ticsg324-1L, 100 MHz, 0.2 ns uncertainty:

| Configuration | DSP48E1 | Core LUTs (including SRLs) | Core fabric FFs | Additional harness FFs | Setup slack | Hold slack |
|---|---:|---:|---:|---:|---:|---:|
| P4 | 16 | 314 | 178 | 229 | +3.218 ns | +0.016 ns |
| P8 | 64 | 808 | 764 | 428 | +1.621 ns | +0.001 ns |

No BRAM is included. P8 has 38 SRLs within its 808 LUT total. Hierarchy
optimization can move logic across module boundaries, so these are the
reported placed hierarchy totals, not hand-counted RTL resources.

The timing top registers the core inputs and outputs to model synchronous
producer/consumer logic. It times every internal register path, including
neighbor-to-core and core-to-neighbor paths. Only the artificial harness's
external pin paths are excluded. `check_timing` reports zero unconstrained
internal endpoints and zero multiple-clock pins; P8 lists 147 input and 281
output pins intentionally excluded. The methodology reports contain zero
checks. Vivado still emits out-of-context port-routing/connectivity warnings
because these harness pins have no physical partition/board locations.

This is evidence of **core timing feasibility**, not full board timing
closure. The 0.001 ns P8 hold slack is positive but very small; synthesis,
placement, clock distribution and hold repair must be repeated with the
actual banks/platform. No Fmax or hardware throughput is inferred.

The P8 worst setup path is the registered reset/launch-clear control through
three logic levels to a PE's DSP accumulator reset. Its 7.576 ns data delay
is mostly routing (6.720 ns). This points to high-fanout control distribution
as a likely integration concern, rather than the arithmetic multiplier.

## A timing issue found and corrected

The initial controller calculated drain selection combinationally from K
and elapsed cycles. Under the first direct-interface timing model (2 ns
external delay), the P4 K-register-to-output path had seven logic levels and
-0.422 ns slack. Its source and report are retained in
[initial_timing](initial_timing/).

The controller now registers drain enable and the row counter. It preserves
all D-02 edges, including final drain at K+3P-1, and passed the full regression
again. The timing environment was subsequently improved to use explicit
same-clock neighbor registers: an intermediate direct-I/O model mixed ideal
external arrival times with routed clock insertion and reported input hold
failures. Thus the original and final slack values are **not a controlled
before/after performance measurement**. They document the diagnosis, RTL
change and final, scoped feasibility check.

## Preserved native platform

The repaired native build synthesized the existing DDR/UART design and
passed the check for exactly one primary clock on CLK100MHZ. Reports and
the final console are in [native_baseline](native_baseline/). The original
clock wizard owns that source clock; the duplicate top-XDC definition was
removed, and the RX synchronizer now has ASYNC_REG.

This run reused the local Vivado installation and available IP products.
It establishes reconstructed-project synthesis, not yet a cache-free build
on another machine. The first reconstruction lacked board identity and
locked the imported IP; board selection is now explicit before import for
fresh projects and re-applied when reopening one. The final log includes
initial lock notices while reopening the earlier partial project, followed
by successful synthesis after the board is applied.

No new native bitstream was routed/programmed in this step. The script's
implementation/bitstream branch awaits a full run and constraint review.
The original stored bitstream remains intact and identifies the earlier
hardware demonstration. Neither it nor native synthesis is a GEMM board test.

Reproduce using [docs/testing.md](../../docs/testing.md). Subsequent integration
results are recorded under [operand_memory](../operand_memory/README.md) and
[tile_engine](../tile_engine/README.md).
