# Architecture

The repository contains a local matrix engine, a UART-controlled BRAM preview,
and a separate native-DDR board baseline. DDR2 is not yet connected to GEMM.

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

## UART-controlled BRAM preview

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

The implemented preview runs one active job with serial load/compute/readback.
It uses synchronous 64-bit operand banks and banked 32-bit results. COBS/CRC
validation and a last-request replay cache protect the command boundary.
The Python host uploads complete inputs and checks every result. The preview
has its own identity and [documented smaller limits](preview.md); it does not
advertise DDR readiness.

Packet decoding uses a shared RAM read port, captured header/trailer fields
and registered CRC input. The response producer holds its payload until the
transport has buffered it, avoiding a duplicate full-packet register bank.
These control-path choices matter to resource use and routed timing even
though UART throughput is much lower than the compute clock.

The board wrapper uses the 100 MHz oscillator through IBUF/BUFG. UART input
and the reset button each enter a three-flop synchronizer. Button assertion
and release are sampled synchronously so BRAM control paths remain timed;
register INIT values hold reset at configuration. This reset choice assumes
a free-running clock and must be reviewed when MIG/clock generation is added.

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

The [burst primitive](axi-burst.md) now supplies independently buffered read
and write transactions at the 64-bit master boundary. It reserves storage
before issuing reads, collects a full write burst before AW/W, and holds
outstanding obligations through faults. A byte-addressed AXI RAM and separate
adversarial responders test this portable module. It does not generate matrix
addresses or manage tile ownership.

The [AXI MIG simulation](axi-platform.md) uses the saved DDR2 pin/timing
configuration with a 128-bit interface at a measured 50 MHz model clock.
Vendor traffic and a DDR2 model test that controller separately. The vendor
width/clock bridge and physical reset wrapper are the next integration boundary.

The scheduler will reuse operands over T-by-T macrotiles and manage the two
operand sets plus two result sets for overlap. The RTL master is 64-bit AXI at
100 MHz. The integrated AXI platform, matrix DMA, external-memory macrotiles, overlap,
and full descriptor/completion semantics remain to be implemented. The framed
host transport is shared with the preview. See [specification.md](specification.md).
