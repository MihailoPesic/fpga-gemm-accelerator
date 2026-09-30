# Architecture

The repository contains a local matrix engine and a separate native-DDR board
baseline. They are not yet integrated on the board.

## Existing compute core

```text
Prepared A / BT vectors
          |
          v
+------------------- gemm_microtile -------------------+
| Start validation, K/shape snapshot, fixed schedule    |
|                                                     |
| Lane masks -> row/column delays -> gemm_array         |
|                                       |             |
|                              P x P gemm_pe instances |
|                                       |             |
|                              Completed row selection |
+---------------------------------------|-------------+
                                        v
                              Result row + valid mask
```

P is 4 or 8; K is 1..256. Each PE retains one signed INT32 accumulator.
Signed INT8 A operands move right and B operands move down through registered
hops. The feeder delays lane q by q cycles so matching reduction indices meet.
BT stores columns of B as rows: `BT[j,k] = B[k,j]`.

```text
C[i,j] = sum(A[i,k] * BT[j,k], k=0..K-1)

Pair k sampled by PE(r,c):  edge 1 + k + r + c
Product committed:         edge 2 + k + r + c
Final drain:               edge K + 3*P - 1
```

The caller supplies K consecutive input vectors and accepts P scheduled result
rows. There is no pause within an active microtile. The surrounding system
must prepare operands and reserve result space before launch. The complete
signal contract is in [compute.md](compute.md).

## Existing operand wrapper

`gemm_bram_microtile` adds two sets of A/BT operand banks and current/next-word
prefetch around the core. Loads are 64 bits; compute consumes one byte per bank
per clock through synchronous BRAM reads. P=4/8 and T=8/32 pass simulation and
synthesize to block RAM with one DSP per PE. The computing buffer is protected
against writes while the other buffer may be loaded. See [memory.md](memory.md)
and the [integration evidence](../results/operand_memory/README.md).

## Existing board baseline

```text
Python <-> USB-UART <-> uart_ddr_bridge <-> native MIG <-> DDR2
```

The UART and bridge run on MIG's 50 MHz user clock; memory data is 128 bits
wide. The saved DDR clock is 200 MHz. This path supports PING and 16-byte memory
reads/writes. It contains no GEMM job controller. Its protocol and host driver
are retained for platform checks, not as the full GEMM protocol.

## Existing local matrix engine

```text
64-bit operand load -> A / BT banks -> prefetch -> P4/P8 compute
                              ^                     |
                         tile scheduler             v
                              |                 C result banks
                         cycle counters             |
                                              64-bit result read
```

`gemm_tile_engine` runs all required microtiles for M,N up to T and K up to 256.
Results are stored in banked BRAM with row/column tail masks. A single-outstanding
read interface holds its response stable under backpressure. The serial engine
blocks host memory access while a job is active and freezes counters at the
final result write. See [tile-engine.md](tile-engine.md).

## Next release

```text
Python <-> UART commands <-> Preview control and cycle counters
                                     |
                      +--------------+--------------+
                      v                             v
               A / BT BRAM                      C BRAM
                      |                             ^
                   Prefetch                         |
                      +----> P4 compute core -------+
```

Start with one active job and serial load/compute/readback. Use synchronous
64-bit operand banks and banked 32-bit results. The local memory, compute,
scheduling and result-read interfaces are implemented. The UART command layer,
host application and board wrapper remain. Timing must pass with those blocks.
The preview has its own identity and documented smaller limits.

## DDR-backed target

```text
Python <-> UART transport <-> Registers / scheduler

DDR2 <-> AXI MIG <-> Vendor width/clock conversion <-> AXI DMA
                                                       |
                  +------------------------------------+---------+
                  v                                              ^
          A / BT local banks                             C local banks
                  |                                              ^
               Prefetch                                          |
                  +----------> P4/P8 compute ---------------------+
```

The scheduler will reuse operands over T-by-T macrotiles and manage the two
operand sets plus two result sets for overlap. The RTL master is 64-bit AXI at
100 MHz. The new AXI MIG platform, DMA, macrotiles, overlap, and host protocol
remain to be implemented. See [specification.md](specification.md).
