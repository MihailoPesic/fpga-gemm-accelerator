# Architecture

The current board image is `0x9d4beb4d`: Nexys A7-50T, P8/T32,
`READ_SLOTS=4`, `ENABLE_OVERLAP=1`, development VERSION `0x200`,
100 MHz core and 1 Mbaud UART. It runs one matrix job at a time, with either
serial MODE0 or overlapping MODE1 scheduling on the same image.
The host supplies signed INT8 A/B and reads signed INT32 C.

Board operation is qualified after cold power-up. Warm-reset DDR timing
remains unresolved. [Status](status.md) records the remaining release limits;
[measurements](measurements.md) separates measured results from targets.

## Control and data paths

```text
Python: pack / upload / configure / START / wait / read C / compare
                              |
                         USB-UART, 1 Mbaud
                              |
                   COBS / CRC32 / sequence replay
                              |
                    gemm_ddr_core, 100 MHz
                     /                     \
     registers + gemm_ddr_overlap_job    idle host memory commands
                     |                         |
           gemm_tile_scheduler                 |
           tags + buffer owners                |
             /               \                 |
          compute          load/store          |
             |                 |               |
     gemm_tile_engine <-> gemm_tile_dma_duplex  |
       A/BT/C banks         bank transfers      |
                               |               |
                               +-------+-------+
                                       |
                             host/DMA ownership mux
                                       |
                              gemm_axi_burst
                         four read slots / one writer
                                       |
                             64-bit AXI, 100 MHz
                                       |
                     SmartConnect width conversion + CDC
                                       |
                             128-bit AXI, 50 MHz
                                       |
                                      MIG
                                       |
                             DDR2, x16 / 200 MHz
```

The scheduler drives three independent operations: operand loading, local
compute and result storage. Their data paths are:

```text
Load:    DDR2 -> MIG/SmartConnect -> AXI read buffers -> DMA -> A/BT banks
Compute: A/BT banks -> prefetch -> lane skew -> 8x8 array -> drain -> C banks
Store:   C banks -> DMA -> AXI write buffer -> SmartConnect/MIG -> DDR2
```

Host uploads and readback share the burst engine only while the accelerator
is idle. Ownership cannot switch while external AXI obligations or buffered
local data/completions remain. Packet checks precede command execution;
descriptor validation precedes accepted START and DMA.
The host polls status, downloads C and checks every useful result and memory
guard. Polling does not drive the hardware schedule.

Project RTL implements compute, banks, DMA, scheduling, registers and packet
handling. AMD SmartConnect and MIG supply bus width/clock conversion and the
DDR controller/PHY. The [board wrapper](../platform/nexys_a7/gemm_ddr_top.sv)
connects these through the [portable core](../rtl/control/gemm_ddr_core.sv).

## Arithmetic, layout and reuse

```text
BT[j,k] = B[k,j]
C[i,j]  = sum(A[i,k] * BT[j,k], k=0..K-1)

A address  = A_BASE  + i*A_STRIDE  + k
BT address = BT_BASE + j*BT_STRIDE + k
C address  = C_BASE  + i*C_STRIDE  + 4*j
```

Each accepted job overwrites the useful C elements.
M/N range from 1 to 1024 and K from 1 to 256. Bases and row strides are
64-byte aligned. The three conservative allocations, including row padding,
must be disjoint and fit inside the 128 MiB DDR window. Validation uses widened
arithmetic before narrowing dimensions or issuing requests.
Inputs are read for `round_up(K,8)` bytes per valid row; hardware masks k>=K.
Padded rows/columns do not cause invalid row reads. C stores enable exactly
the useful bytes, leaving output padding and guards unchanged.

P=8 is the physical array dimension; T=32 is the local macrotile dimension.
The scheduler visits row-major T-by-T output regions. Each region retains
its full K reduction locally and launches only nonempty P-by-P microtiles.
No external partial-sum storage is needed within the supported K range.

For a full T32 macrotile at K=256, loading 32 A rows and 32 BT rows transfers
16,384 input bytes. Sixteen 8x8 launches reuse them to produce 1,024 INT32
results, or 4,096 output bytes. A travels right and B down through the array;
each PE holds one output accumulator. This provides reuse inside the mesh
and across microtile launches. BT packing makes reduction rows contiguous;
lane skew is the separate timing operation that aligns their arrivals.

## Banks and the fixed compute schedule

Two A/BT sets and two C sets live in banked BRAM. Operand banks use 64-bit
words so DMA deposits eight reduction elements per word; compute extracts
one byte per bank per cycle. A and BT each have P banks. C drains P results
into distinct banks in one cycle and returns adjacent INT32 pairs to the writer.

```text
Operand bank   = q mod P
Operand word64 = buf*(T/P)*32 + floor(q/P)*32 + floor(k/8)
Operand byte   = k mod 8

C bank         = column mod P
C word32       = buf*(T*T/P) + row*(T/P) + floor(column/P)
```

Here q is a local A row or BT output column; buf names the corresponding
input or result set. These are separate namespaces.
Current/next-word registers hide synchronous BRAM latency at eight-element
boundaries. The destination microtile is reserved and prefetch completes
before launch. DDR backpressure can delay a future launch, but cannot pause
one already in flight.

Each PE has a signed registered product followed by a signed INT32
accumulator. With edge 0 as launch/clear:

```text
Pair k sampled at PE(r,c): edge 1 + k + r + c
Product committed:        edge 2 + k + r + c
Final row drain:          edge K + 3*P - 1
```

The operand wrapper adds prefetch and launch overhead; those core edges
are not whole-job latency. [Compute](compute.md), [operand memory](memory.md)
and [the local engine](tile-engine.md) define the exact signal, BRAM and
result-read schedules. Reset clears valid/ownership state, not all BRAM data.

## Buffer ownership and overlap

```text
Input set: FREE -> FILLING -> READY -> COMPUTING -> FREE
Result:    FREE -> COMPUTING -> READY -> WRITING -> FREE
```

Input and result IDs are chosen independently and carry a tile tag and
captured addresses/shape. A and BT must both finish loading before an input
becomes READY. Compute selects the next ordered input and reserves a complete
FREE result set. Input ownership spans every microtile that reuses its data.
A result becomes READY after the local matrix operation completes and remains
WRITING through its DMA terminal handshake, including a held completion
after the final successful B response.

MODE0 waits for the previous tile to retire before admitting the next.
MODE1 loads a following tile and writes a completed predecessor when their
independent buffers and memory channels permit:

```text
stage       tile t-1          tile t          tile t+1
load                                          A/BT
compute                       array
store       C -> DDR
```

This is an ownership sketch, not equal-length or cycle-exact intervals.
Two blocked results can prevent the next compute launch; they cannot force
overwriting live C or interrupt an active microtile. Store progress depends
on completed C and memory readiness, not on loading the next input.
The [tagged scheduler](tile-scheduler.md) and
[overlap job contract](ddr-overlap.md) specify admission, retirement and faults.

## DMA, completion and faults

The 64-bit AXI master uses one ID and INCR bursts of 1..16 eight-byte beats.
Row planning splits at row end, 16 beats and the next 4 KiB boundary. Four in-order read
slots reserve complete response storage and local routing metadata before AR.
The engine validates a complete read burst before publishing its words to
banks; credit remains owned through local delivery and terminal completion.
The writer gathers a complete burst before
offering AW/W and retains one outstanding write through B. Address and data
channels handshake independently and hold stalled payloads stable.

START acknowledges the validated job's acceptance, not its completion.
Configuration and allocations are checked before acceptance. BUSY rejects
another START or a concurrent host memory command rather than queuing work.

| Boundary | Completion event |
| --- | --- |
| Microtile | Final scheduled drain row captured; not DDR completion. |
| Local macrotile | All useful results captured in C banks; input may be released, C remains owned by the scheduler. |
| Result store | Successful final B and the DMA terminal completion consumed. |
| Public job DONE | All tiles retired, compute/DMA idle and local/external burst obligations drained. |

JOB_CYCLES retains the timestamp difference from validated START acceptance
to the final successful result B handshake. Public DONE can appear later
while local completion propagates. Counters freeze before software observes
DONE; host transfers are excluded. [Measurements](measurements.md) describes
the performance scopes and [registers](ddr-registers.md) the software map.

Memory response, protocol, watchdog and calibration-loss errors latch the
first fatal code and RESET_REQUIRED. A fault wins over simultaneous success.
New work stops, while accepted compute, bank and AXI obligations retain their
owners and drain where possible. A stalled AXI VALID is never withdrawn to
abort the job. A hung interface can leave BUSY asserted with ERROR visible;
partially written C is invalid. CLEAR_STATUS cannot remove a fatal reset
requirement. Reset must coordinate the core, conversion and memory platform.
See [AXI transactions](axi-burst.md), [row planning](dma-rows.md),
[tile DMA](tile-dma.md) and [the public fault contract](ddr-overlap.md).

## Clocks, resets and physical boundary

```text
Board oscillator, 100 MHz
          |
      Clock Wizard (CPU reset does not reset this wizard)
          +-- core/system 100 MHz -> custom RTL + SmartConnect core side
          |                       -> MIG sys_clk_i
          +-- reference 200 MHz ---> MIG clk_ref_i
                                      |
                                 MIG internal clocks
                                      +--> DDR2 clock, 200 MHz
                                      +--> ui_clk, 50 MHz
                                             MIG AXI + SmartConnect UI side

CPU_RESETN AND Clock Wizard locked -> MIG sys_rst (active low)
MIG ui_clk_sync_rst -> reset_core @ 100 MHz -> core_rst
                   -> reset_ui @ 50 MHz ---> MIG/bridge AXI reset
MIG calibration -> three-flop single-bit synchronizer -> core ddr_ready
```

Vendor per-domain reset blocks observe lock and release resets synchronously
on their own clocks. The wrapper gates DDR_READY with `!core_rst`.
SmartConnect owns the AXI payload CDC; the calibration status crosses
separately. Calibration returning after a recorded loss does not clear the
fatal state. A reset button event restarts memory initialization while DDR
may remain powered, which is why it is not equivalent to cold power-up.

The [platform generator](../scripts/create_axi_platform.tcl) and
[wrapper](../platform/nexys_a7/axi_ddr_platform.sv) preserve the clock/reset
network and DDR pin configuration. [Platform configuration](axi-platform.md)
and [memory compatibility](ddr-memory-compatibility.md) distinguish the MIG
preset/model from the incompletely identified physical device suffix.
The [current routed record](../results/ddr_overlap/release_1mbaud/t32/README.md)
contains timing, CDC, constraints and the worst control path. Passing those
reports does not qualify warm reset or establish electrical margin.

For use and reproduction, follow the [host API](host-gemm.md),
[board procedure](ddr-board.md) and [verification map](verification.md).
Earlier interfaces and separately qualified images are indexed in
[development history](history.md).
