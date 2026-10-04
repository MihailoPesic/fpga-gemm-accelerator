# Tile DMA adapter

`rtl/memory/gemm_tile_dma.sv` connects the [row sequencer](dma-rows.md)
to the [AXI burst primitive](axi-burst.md) and the
[local matrix engine](tile-engine.md). One operation loads A, loads BT or
stores C. A surrounding controller sequences these operations and starts
compute after both operands have been loaded.

```text
DDR / AXI RAM <-> gemm_axi_burst <-> gemm_tile_dma
                                        |
                    +-------------------+-------------------+
                    |                                       |
             64-bit A/BT loads                       C pair reads
                    |                                       |
                 A/BT banks --> prefetch --> array --> C banks
```

The adapter uses the existing burst buffers. It does not contain another
burst-sized data FIFO or implement AXI channels. Row addresses, page splits
and byte masks come from `gemm_dma_rows`. P=4/8 and T=8/32 select the existing
engine geometry; the adapter moves 64-bit words independently of P.

## Operation contract

All interfaces share one clock with synchronous active-high reset. The
request handshake snapshots the address/shape fields and buffer selection:

| Field | Meaning |
| --- | --- |
| `req_write` | 0 loads an operand; 1 stores results |
| `req_bt` | Load A=0 or BT=1; unused for result stores |
| `req_buf` | Input or result buffer set, 0/1 |
| `req_base`, `req_stride` | First row address and bytes between rows |
| `req_rows` | Useful local rows, 1..T |
| `req_row_bytes` | Useful bytes per row: K for operands, 4*N for C |

The row sequencer checks alignment, dimensions, stride and the rounded
transfer footprint against the DDR window. The adapter additionally rejects
rows beyond T and C rows larger than 4*T bytes. Rejection returns BAD_DESC
(3) without issuing AXI or local-bank transfers. Full job-allocation bounds,
64-byte descriptor alignment and pairwise disjointness belong to the job
controller, rather than this tile-local interface.

Each read loads `round_up(K,8)` bytes per useful row. Byte padding may be
nonzero; compute masks reduction indices at or beyond K. For A, the row
metadata selects a local matrix row; for BT it selects a local output column.
The load word index is the burst's first word plus the returned beat index.
No padded matrix row is fetched. Operand words and metadata hold stable
while the engine's load port is stalled.

A store reads adjacent C values through the engine's synchronous result port.
The caller supplies the completed tile's M rows and full 4*N-byte row width;
prefix stores are outside this contract. The adapter does not independently
reconstruct the completed shape.
There is one outstanding local read. Its held response supplies one beat to
the burst engine. The expected byte mask is `0xff`, except `0x0f` for an odd
final INT32 value. Result padding remains untouched. The adapter checks the
engine's response mask as well as its error flag.

There is one operation at a time. The default `READ_SLOTS=1` allows one burst;
`READ_SLOTS=4` permits four read commands while keeping writes serial.
Read completion follows the
last local-bank delivery; write completion follows the AXI B response.
`done_valid/done_status` hold until `done_ready`. The caller retains input or
result-buffer ownership through that handshake and must not start compute
or reuse a stored result buffer prematurely. Loaded operands remain owned
through the subsequent compute operation; a load completion only marks them ready.

## One-read schedule

The burst engine validates a complete read burst before offering its data
to the adapter. For an L-beat successful read with no local stalls, let F
be the edge accepting the last AXI R beat:

```text
Edges              Action
F+1 .. F+L         Burst buffer delivers validated words
F+2 .. F+L+1       Adapter deposits words in operand banks
F+L+2              Local read completion is accepted
```

The held operand word allows a bank deposit and the next buffered word's
acceptance on the same edge. Bank delivery therefore sustains one word per
cycle. The serial planner waits for terminal completion before another
burst; receiving DDR data and copying a subsequent burst do not overlap.

Result gathering has a separate cadence. With an accepted write command
at edge 0 and no local stalls:

```text
Edge   Action
1      Accept the C-bank read request
2      Register the synchronous bank response
3      Accept that response into the adapter
4      Collect the write word in the burst buffer
8      Collect the next write word
```

Thus L result words take `4*L` cycles to collect before AW/W can begin.
The complete buffer protects independent AXI address/data handshakes and
retains C ownership through B. The accepted command and each nonfinal word
now lead directly to the next C request. `NEXT_PAIR` remains a fault-fill
state: after a fault, remaining words use zero byte strobes. Already offered
C requests and responses retain their original handshake obligations.
The [cycle profiler](ddr-core.md#cycle-attribution) records these boundaries
alongside memory response delays and reconciles them with job counters.
The [original profile](../results/ddr_gemm/profile/README.md) records the
previous five-cycle cadence under its original source hashes. Its board
identity does not qualify this changed adapter.

## Four-read schedule

`gemm_tile_dma_read_queue` reserves four metadata entries, each containing
the row, first word, beat count and final-burst marker. Command acceptance
reserves an entry on the same edge as the burst engine's complete data buffer.
The two-bit ring index is an opaque local tag. Responses and completions are
checked against the oldest entry; repeated tags are safe only after retirement.

```text
row planner --> reserve metadata + burst buffer --> AR --> ordered R
                     |                              |       |
                     |                        four validated buffers
                     |                                      |
                     +--> oldest tag/row/word <-- local DATA + DONE
                                      |
                                held bank word --> A/BT bank
                                      |
                           last bank acceptance + DONE
                                      |
                              retire entry / free credit
```

DDR can receive later bursts while validated words from an older burst enter
the banks. One held bank word sustains one delivery per cycle and remains
stable under backpressure. The metadata entry retires only after that word
drains and the ordered terminal is consumed. No separate burst-sized FIFO
is added to the adapter.

A fatal condition stops new planner commands and cancels never-offered AXI
reads through the burst engine's read-only cancellation input. Held AR,
already accepted responses, bank-word offers and terminal payloads retain
their obligations. The job keeps its first fatal code, drains all accepted
local commands and cannot report success. Load, compute and store operations
remain serial at the macrotile level; this change overlaps read reception
with buffered bank delivery.

## Errors and reset

Memory response errors and burst protocol errors propagate from the burst
engine. Unexpected local read metadata or an invalid C response latches
PROTOCOL (8) in the adapter. Fatal state blocks new operations until a
coordinated platform reset. The job controller must observe fatal state
separately from already offered completion records.

The write command is accepted before the C pairs are collected. If a local
C response fails, the current and remaining beats of that burst use zero
data and zero byte strobes. This supplies the required beat count so the
accepted command can drain; it does not repair any previously collected
result beats. Earlier bursts or valid beats may already have modified C,
which is invalid after a fatal error. No later burst is issued.

A legitimate burst-engine terminal write completion releases a pending local
data offer. A premature success completion is a protocol fault; it does not
retire an accepted write before collection and the actual memory response.
An already offered or accepted C read remains an obligation: its
address holds until acceptance and its response is consumed before releasing
the write completion. AXI VALID signals are owned by the burst engine and
retain its existing fault/drain contract.

Reset clears control and ownership, rather than BRAM contents. It must be
coordinated with the banks, compute, AXI conversion and MIG; resetting the
adapter alone cannot cancel a memory transaction.

## Verification boundary

`make test-tile-dma PYTHON=.venv/bin/python` runs the portable integration
fixture with the real engine, banks, row sequencer and burst primitive.
The [current one/four-read regression](../results/ddr_gemm/read4/portable/tile/record.json) passes 56 tests across P4/P8 and T8/T32:
216 complete jobs, 21,938 output comparisons and 128 invalid requests.
Cases check read credits, ring metadata, held bank/DONE ownership and queued
fault draining. Write tests inject faults exactly
on write-command and nonfinal data-word acceptance, checking held C requests,
response backpressure, zero-strobe suffixes and the final B obligation.
The [C gather regression](../results/ddr_gemm/gather4/regression/README.md),
[pipeline regression](../results/tile_dma/pipeline/README.md) and
[original checkpoint](../results/tile_dma/README.md) retain their earlier
source identities and results.
The memory side is a behavioral AXI RAM. This fixture does not contain MIG,
UART job control, external macrotile iteration or overlap, and it does not
establish board timing or DDR-backed hardware performance.

## Independent load and store contexts

`rtl/memory/gemm_tile_dma_duplex.sv` composes two unchanged adapters at the
local burst boundary. The reader always loads operands and uses the selected
`READ_SLOTS=1/4`; the writer always stores C and allows one write burst at a
time. Each owns its row planner, captured descriptor, busy state and terminal
response. It shares the existing burst engine's independent read/write paths.
No AXI handshake is implemented in this wrapper.

```text
                 gemm_tile_dma_duplex
              +------------------------+
load_req ---> | reader context         | ---> burst read engine ---> DDR
load_done <-- | A or BT, input buf 0/1  | <--- validated read data <--- DDR
              |        |               |
              |        v               |
              |      A/BT banks         |
              |                        |
store_req --> | writer context         | ---> burst write engine --> DDR
store_done <- | C, result buf 0/1       | <--- final B response <----- DDR
              |        ^               |
              |        |               |
              |      C pair reads      |
              +------------------------+

Legal simultaneous ownership with CONCURRENT_PORTS=1:

next tile:      load A/BT into input1
active tile:    input0 --> array --> result1
previous tile:  read result0 --> store to DDR
```

Input and result IDs name different memories. Loading input1 while computing
into result1 is legal when compute uses input0. The caller must establish
these lifetimes; the DMA wrapper does not track tile identities or decide
when a buffer becomes READY/FREE. Both A and BT must complete before launch.
The compute engine still excludes its active input/result sets for the entire
macrotile, and an accepted C read retains its result owner through response
consumption.

`load_req_*` and `store_req_*` carry the same descriptor fields as the serial
adapter, except direction is fixed and `load_req_bt` selects A/BT. Their
handshakes and `load_done_*`/`store_done_*` responses are independent. Both
requests can be accepted on one edge; a held completion in one context does
not prevent another operation in the other. BAD_DESC=3 affects only the
invalid request and performs no transfers for it. The store caller still
supplies the complete useful C shape, including odd-column strobes.

Aggregate `busy` is `load_busy || store_busy`, including held operation
responses. A future scheduler must also check compute ownership, burst
`local_idle` and `axi_quiescent` before returning the memory path to the host
or publishing job completion. Busy alone does not establish external-memory
quiescence.

### Shared fatal state

A fatal condition immediately blocks both descriptor handshakes and both
unaccepted local burst-command handshakes. These local commands are
cancelable before acceptance. Accepted data, held operand words, C read
requests/responses and operation completions remain connected so their
obligations can drain. The burst engine owns AR/AW/W: the wrapper never
withdraws its stalled AXI VALID signals.

The first error is captured on the wrapper's observation edge, with priority
external memory, reader, then writer when several sources are present on
that edge. Valid fatal codes are 7..10; other external values normalize to
PROTOCOL=8. The captured code remains frozen until coordinated reset. Before
capture, the outward code follows this combinational priority selection.

Each existing adapter includes its memory-fatal input in its fatal output.
Directly feeding the aggregate output back into both adapters would create a
combinational loop. The wrapper therefore passes external fatal or its
registered first error to the children. A local child fault reaches its
sibling's internal stop on the following observation edge; immediate wrapper
admission guards cover that interval. Work accepted on the detection edge
remains owed.

An already held successful DONE record remains unchanged if the sibling
later faults. Aggregate fatal invalidates job success; the scheduler must
check it separately from individual DONE payloads. A partially written C
matrix remains invalid after a fatal event.

### Verification and integration status

`make test-tile-dma-duplex PYTHON=.venv/bin/python` connects the wrapper to
the production banks/array with `CONCURRENT_PORTS=1` and a behavioral AXI RAM.
It checks full signed-matrix outputs, nonzero input padding, output guards,
address/page splits, descriptor snapshots, independent completions and
cross-direction fault draining. Test-only gates stall local interfaces;
AXI channel pauses are independent.

The [saved checkpoint](../results/tile_dma_duplex/README.md) passes 40 duplex
tests across all eight configurations, with 48 fully checked stored matrices
and 12,728 outputs. Seven serial DMA and ten serial packet/core compatibility
tests pass separately. Raw summaries, coverage, XML, tool versions and
unchanged source hashes are preserved with actual process exit records.

The production DDR core still instantiates the serial adapter and selects
`CONCURRENT_PORTS=0`. MODE=1 remains rejected. Tagged macrotile ownership and
independent load/compute/store cursors are the next integration step;
portable DMA concurrency alone does not qualify a new bitstream.
