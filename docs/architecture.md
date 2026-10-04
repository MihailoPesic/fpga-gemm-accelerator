# Architecture

The selectable DDR GEMM build runs complete matrix jobs on the board:
framed UART commands load DDR inputs, the validated job controller and tagged
scheduler coordinate DMA and local compute, and the host compares returned results. The same
portable modules run against AXI RAM; SmartConnect and MIG provide the board
memory boundary. The earlier BRAM preview and DDR diagnostic retain their
separate evidence. See [status.md](status.md) for the dated checkpoint and
[README.md](README.md) for the documentation index.

| Build | Working behavior | Evidence boundary |
| --- | --- | --- |
| BRAM preview | UART loads A/BT, P4/T32 computes C, host compares every result | 180 board jobs, 90,510 compared outputs; no DDR |
| AXI DDR diagnostic | UART starts pattern/compare traffic through the custom burst engine, SmartConnect and MIG | Three board runs after cold power-up; no GEMM |
| Row burst sequencer | Plans row addresses, burst splits and final-byte masks | Four standalone metadata tests; data movement is tested in the adapter below |
| Serial tile DMA integration | Loads A/BT, runs the production GEMM engine and stores C through AXI RAM | 20 portable tests across four builds; 108 jobs and 11,039 compared outputs; no MIG or board timing |
| Serial DDR job integration | Validates/snapshots descriptors and iterates external macrotiles through DMA/compute/store | 154 AXI RAM jobs, 86,322 checked outputs; register unit tests are separate |
| Serial DDR packet subsystem | Framed commands upload A/BT, configure/run jobs and download C through shared host/DMA ownership | 24 tests, 28 jobs and 5,056 checked outputs across four builds; byte transport and AXI RAM |
| Native DDR baseline | Historical UART bridge using MIG's native application port | Retained platform history; different protocol/address interface |
| Serial DDR GEMM board | UART/register commands and shared host/DMA access connect the complete matrix path to MIG | P4 `0x4898db67` and P8 `0x01caf61c`, T32 at 100 MHz: each passes vendor/routed gates and 48 board jobs with 49,593 outputs checked; cold start only. Original P4 also passes the maximum-shape check |
| Concurrent read DMA board | Four validated read buffers and ordered bank metadata feed the same serial macrotile schedule | P8/T32 `0xd558a543`: vendor, routed/reset review and all 48 board jobs pass; dense median 4.529 useful GOPS at 100 MHz |
| Selectable overlap board | Independent DMA and two operand/result sets overlap load, compute and store under tagged ownership | P8/T32 `0x2c680af7`: 156 jobs, 344,946 compared outputs; 64x64x256 overlap median 8.345 GOPS and 1.837x paired speedup at 100 MHz |

These are build configurations of one repository, selecting top modules and
shared RTL. Generated Vivado projects live under `build/`; integration connects
module interfaces rather than merging generated project directories. The
[preview](../results/preview/README.md) and
[DDR platform](../results/ddr_platform/README.md) have separate bitstream identities.

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
Transposition makes the k dimension contiguous for both operands in memory;
skew is a separate timing operation after those bytes have been fetched.
A value is reused across PE columns and a B value across PE rows. Each PE's
product register changes every clock, but only its registered valid product
may update the accumulator on the following edge.

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
Every hop and the schedule advance on each clock. A late DDR word therefore
cannot simply delay one lane: the other operands would continue moving and
the scheduled drain would still arrive. Local prefetch absorbs memory latency
before launch; validity marks scheduled data, not permission to stall the mesh.

## Existing operand wrapper

`gemm_bram_microtile` adds two sets of A/BT operand banks and current/next-word
prefetch around the core. Loads are 64 bits; compute consumes one byte per bank
per clock through synchronous BRAM reads. P=4/8 and T=8/32 pass simulation and
synthesize to block RAM with one DSP per PE. The computing buffer is protected
against writes while the other buffer may be loaded. See [memory.md](memory.md)
and the [integration evidence](../results/operand_memory/README.md).

Buffer lifetime depends on the boundary. The wrapper blocks writes to the
selected input set until a microtile finishes. The local engine holds those
operands for the entire job because later microtiles reuse them, and blocks
all public loads while busy in its default serial configuration. The opt-in
`CONCURRENT_PORTS=1` interface permits other-buffer loads and result reads;
its [portable verification](../results/tile_overlap_ports/README.md) covers
independent input/output IDs and response ownership. The default DDR build
selects serial ports; `ENABLE_OVERLAP=1` selects concurrent ports. A selected result set is invalidated at START;
it becomes locally readable only after the final result row is written into
C BRAM. That local validity does not release DDR-store ownership: the serial
job controller preserves the result set until its last successful B response
and DMA completion. Reset clears ownership state, not all BRAM contents.

## Historical native-DDR baseline

```text
Python <-> USB-UART <-> uart_ddr_bridge <-> native MIG <-> DDR2
```

The UART and bridge run on MIG's 50 MHz user clock; memory data is 128 bits
wide. The saved DDR clock is 200 MHz. This path supports PING and 16-byte memory
reads/writes. It contains no GEMM job controller. Its protocol and host driver
are retained for platform checks, not as the full GEMM protocol.
The current integration path is the byte-addressed AXI platform below. Native
`app_addr` values must not be copied into AXI descriptors.

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
blocks host memory access while a job is active in the default configuration
and freezes counters at the final result write. The optional concurrent ports
provide disjoint local accesses without changing that compute schedule.
See [tile-engine.md](tile-engine.md).

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
a free-running clock and belongs to the BRAM preview. The DDR build uses the
generated clock/reset network described below.

## Working AXI DDR diagnostic

```text
Python <--> UART / shared packet transport <--> diagnostic register backend
                                                        |
                                                  START + seed
                                                        v
                                           pattern / compare controller
                                                        |
                                       local command / data / completion
                                                        v
                                            gemm_axi_burst, 64-bit
                                                        |
                    100 MHz core AXI <--> SmartConnect <--> 50 MHz AXI
                                                                  |
                                                           MIG, 128-bit
                                                                  |
                                                    DDR2, 16-bit pins
                                                     200 MHz clock
```

The default `READ_SLOTS=1` [burst primitive](axi-burst.md) supports one outstanding read and one
outstanding write independently. The current serial callers finish one burst
before starting another. It buffers a whole read and checks response status,
ID, beat count and RLAST before publishing any local data; a failed read burst
publishes none of its words. It buffers a whole write before offering AW/W.
Address and data handshakes are independent; write completion requires B.
Faults stop new work while preserving asserted VALID signals and pending obligations. The primitive
neither knows matrix dimensions nor splits row transfers.

The optional four-slot read configuration now passes its
[primitive regression](../results/axi/read_queue/README.md). Allocation and
ordered local completion share a slot ring; accepted ARs have a separate
response-owner FIFO. Complete buffers remain reserved through DONE, and
read protocol faults suppress later unvalidated data. The diagnostic retains
`READ_SLOTS=1`. GEMM builds select one or four slots; the P8/T32 four-read serial
image has vendor, routed and board qualification. The integrated overlap path
shares this read queue with its independent writer.

[SmartConnect and MIG](axi-platform.md) supply width conversion, clock-domain
crossing and physical DDR control. Custom control stays at 100 MHz; MIG's
128-bit AXI interface runs at 50 MHz. These are two sides of one memory path,
not independent memory channels. Calibration crosses back through a three-flop
single-bit synchronizer. Reset coordination includes the master, conversion and
MIG; resetting only the master cannot cancel an issued transaction.

The actual clock and reset structure is:

```text
E3 oscillator, 100 MHz
          |
       Clock Wizard (CPU reset does not reset this wizard)
          +-- core/system 100 MHz --> custom RTL + SmartConnect core side
          |                      \-> MIG sys_clk_i
          +-- reference 200 MHz ---> MIG clk_ref_i
                                      |
                                  MIG internal clocks
                                      +--> DDR2 clock, 200 MHz
                                      +--> ui_clk, 50 MHz
                                             |--> MIG AXI + SmartConnect UI side

CPU_RESETN AND Clock Wizard locked --> MIG sys_rst (active low)
MIG ui_clk_sync_rst --> reset_core, clocked at 100 MHz --> core_rst
                   \-> reset_ui, clocked at 50 MHz ---> MIG/bridge AXI reset
MIG calibration (UI domain) --> three synchronizer flops --> core ddr_ready
```

The generated per-domain `proc_sys_reset` blocks also observe clock lock and
release reset synchronously on their own clocks. The wrapper gates DDR_READY
with `!core_rst`; calibration is readiness, not an AXI reset substitute.
The reset graph explains why a button event affects more than the custom RTL:
it restarts MIG's clock/memory initialization while the external DDR device
may remain powered. See the
[generator](../scripts/create_axi_platform.tcl) and
[wrapper](../platform/nexys_a7/axi_ddr_platform.sv).

The [diagnostic controller](ddr-diagnostic.md) initializes 16 selected slots,
checks their address-dependent patterns, performs masked overlays and checks
again. Each read word, expected value and address are registered before
comparison. A pending comparison blocks completion, including the final word.
Three physical runs passed, each with 1,024 read and 648 write beats. This checks
4,096 selected bytes including DDR-window edges; it is not a full memory sweep,
bandwidth measurement or DDR-backed GEMM.

The diagnostic passes routed setup/hold at +0.642/+0.027 ns at 100 MHz. Its
[board record](../results/ddr_platform/board/summary.json) uses cold power-up.
An abrupt warm-reset simulation still fails DDR2 clock/CKE timing qualification,
even though calibration and data comparisons recover. These are separate claims.

## Serial DDR-backed GEMM

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

The platform, burst primitive, local banks and compute engine in this diagram
exist. The [row sequencer](dma-rows.md) plans addresses, burst splits and byte
masks. Its one/four-read regression checks 792 descriptor
cases and 9,257 commands. The [tile DMA adapter](tile-dma.md) adds actual bank delivery and
result collection without another whole-burst data buffer. A held operand
word supports simultaneous bank delivery and replacement; C uses one
synchronous pair read at a time into the burst engine's write buffer.

The [current portable integration regression](../results/ddr_gemm/read4/README.md) loads,
computes and stores 216 complete matrices against AXI RAM, comparing 21,938
outputs across all four P/T builds and both read depths. It checks output padding, byte strobes,
page splits and fatal drain behavior. A store completion includes B; a local
result-read error zero-masks the remaining unoffered beats while draining
the accepted command. The affected C matrix is invalid. The adapter retains
already offered bank requests and data through faults.

The [serial job controller](ddr-job.md) now validates complete DDR allocation
ranges, snapshots descriptors and iterates external macrotiles. The
[P8/T32 job regression](../results/ddr_gemm/read4/portable/job/record.json) adds
78 complete matrix jobs with 87,946 compared results across both read depths. Its executed shapes and
source identities are saved; workload generation shares a random stream with
stall injection, so counts across RTL revisions are not a fixed-workload
performance comparison. The
[register block](ddr-registers.md) exposes
configuration and frozen counters through a single-outstanding local bus.
Its unit tests model the job ports; they do not establish UART integration.

The [packet subsystem](ddr-core.md) now connects the real register and job
interfaces to the shared transport. Host MEM_READ/MEM_WRITE owns the burst
engine only while the job engine is idle. Ownership persists through terminal
completion and fault drain, including an accepted write still being collected
before any AXI request. Job counters explicitly exclude host traffic.
The [current complete-path test](../results/ddr_gemm/read4/host_geometry/core/record.json) adds 64 jobs and 10,232
checked outputs across both depths and all four P/T builds through packet upload/configure/START/poll/download. This is
byte-level transport against AXI RAM, not UART pin or vendor DDR verification.
Its 80 tests include page-boundary burst schedules and calibration loss at
host read/write completion. Immediate
fault detection governs memory ownership; registered first-error state governs
the wide reply buffer, with success suppressed on a faulting completion edge.
The [host API](host-gemm.md) has separate Windows/Linux unit evidence.

The serial controller visits T-by-T macrotiles over M,N up to 1024 while
retaining the full K <= 256 reduction locally. It uses buffer set zero and
loads A/BT, computes and stores one tile before moving on. Two input and two
result buffer sets exist, but that alone does not implement overlap: their filling, ready,
computing and writing states must prevent reuse until all consumers finish.
A result set cannot be freed before its last successful write response.
The [board wrapper](ddr-board.md) connects the serial path to MIG. The current
four-cycle C-gather path has complete portable, vendor, routed and physical
qualification for both P4 `0x4898db67` and P8 `0x01caf61c`.
The [matched board comparison](../results/ddr_gemm/p8_scaling/README.md)
holds source code, matrix bytes and clock fixed while changing P.
The source revision adds selectable four-read DMA, with separate issue and
ordered metadata retirement. It overlaps DDR reception with bank delivery.
Build `0xd558a543`
has [four-read board qualification](../results/ddr_gemm/read4/host_geometry/board/README.md):
48 jobs, 49,593 outputs and all guards checked. Its dense median is 11,576.5
cycles (4.529 useful GOPS) at 100 MHz; active compute and traffic match the
saved one-read P8 checkpoint.
See [specification.md](specification.md).

The selectable [duplex tile DMA](tile-dma.md#independent-load-and-store-contexts)
adds separate operand-load and result-store operation contexts over the same
burst engine. With concurrent bank ports, a prepared input set can compute
while another is filled and a completed result set is stored. Completion and
descriptor ownership are independent in the two DMA directions; a shared
first-error latch stops admission while retaining accepted obligations.
The wrapper does not assign tile identities or buffer lifetimes. The internal
[tagged scheduler](tile-scheduler.md) supplies those owners and independent
load, compute and store cursors. It reserves a whole result set before launch,
retains it through successful store completion and supports both serial and
overlap schedules. The default build uses the original serial adapter.
`ENABLE_OVERLAP=1` connects the duplex path and scheduler to the real packet
and register interfaces through `gemm_ddr_overlap_job`. This shell validates
and snapshots descriptors, acknowledges actual START acceptance, excludes
host memory access and implements the watchdog, first fatal error and frozen
job counters. VERSION=0x200 exposes MODE=0/1 on this path. The unchanged
VERSION=0x100 path rejects MODE=1. See [ddr-overlap.md](ddr-overlap.md)
for the complete ownership and counter contract. Its own vendor, routed and
[board gates](../results/ddr_overlap/timing_predicate/final_build/board/README.md)
pass for image `0x2c680af7`; 30 matched pairs at 64x64x256 measure 8.345 useful
GOPS with overlap and a 1.837x median paired speedup over serial mode.

## Completion boundaries

| Boundary | DONE event | What its cycle count excludes |
| --- | --- | --- |
| Local GEMM / BRAM preview | Final result row captured in C banks | Host upload/readback and all DDR traffic |
| DDR diagnostic | Final checked read and terminal completion, after prior writes | UART/host polling; this is not a GEMM count |
| Tile DMA operation | Final local read delivery or final AXI B, then held operation response | Separate compute operation and host transport |
| DDR job controllers | All useful C writes acknowledged successfully and pending obligations drained; cycle endpoint is the final actual B handshake | Descriptor validation before accepted START and host packing/upload/download |

For the local engine:

```text
job_cycles = ceil(M/P) * ceil(N/P) * (K + 3*P + 3)
```

The DDR job adds transfer and scheduling costs. Its independent counters and
final-B timestamp are checked in portable simulation. The
[physical baseline](../results/ddr_gemm/board/README.md), build `0xed44f92e`,
measured median 34,637 cycles for 32x32x256 at 100 MHz across 30 resident jobs:
1.514 useful GOPS including DDR tile transfers and final write acknowledgement.
UART packing, upload and download are outside that counter. These measurements
belong to that saved bitstream. The current P4/P8 images have their own
[physical scaling measurements](../results/ddr_gemm/p8_scaling/README.md):
dense median latency is 34,103 / 21,281.5 cycles, giving 1.60x DDR-job speedup.
The arithmetic ceiling, local-job timing and complete DDR-job timing describe
different boundaries.
