# Local matrix engine

`gemm_tile_engine` computes a complete local matrix job:

```
C[i,j] = sum(A[i,k] * BT[j,k])
1 <= M,N <= T; 1 <= K <= 256; P=4/8; T=8/32
```

The controller reuses loaded operands across every nonempty P-by-P microtile.
Column groups advance first, followed by row groups. Final groups use the
remaining row/column counts. The default BRAM preview is P4,T32.

## Commands and ownership

Load A and transposed B through the 64-bit port described in [memory.md](memory.md).
All needed words must be loaded before START. The engine does not maintain a
bitmap of initialized words. With the default `CONCURRENT_PORTS=0`, loading and
result reads are disabled while busy. There is one compute job at a time in
either configuration. Transfers use valid/ready handshakes.

A START pulse is accepted when `start_ready` and all dimensions are legal.
The engine snapshots dimensions and input/output buffer IDs, clears DONE and
counters, and invalidates the selected result buffer. Other completed results
remain valid. A new accepted START explicitly replaces the selected buffer's
previous results. The production DDR core uses the default serial port contract.

An invalid or busy START pulses `cmd_error` and has no memory effects. It does
not clear DONE, counters or existing results. START has priority over simultaneous
host read/load requests. A pending read response must be consumed before another
START can be accepted, including when it is consumed on the same edge as START.
These priority and response rules describe the default serial configuration.

`CONCURRENT_PORTS=1` allows the scheduler to use the other buffer while a local
job runs. The selected input and result buffers remain reserved throughout the
whole job, including the gaps between microtile launches. A load may write only
the other input buffer while busy. A result read may name only the other result
buffer while busy; it must still refer to a completed result to return data.
This option supplies local ports for future overlap scheduling; it does not
implement a DDR scheduler or enable overlap in the production design.

In this configuration an accepted START may coincide with a load into a
different input buffer or a read from a different result buffer. Requests to
the START's selected buffers are held off. An accepted read owns its result
buffer through the BRAM capture and any held response, including error
responses. START may use the other result buffer during that interval, but it
cannot replace the response-owned buffer until the edge after the response is
consumed. Response data, strobe and error remain stable across other-buffer
launches. The caller still owns input readiness and result-buffer lifetimes.

Input and output IDs name separate memories. For example:

```text
DMA loads A/BT set 1     A/BT set 0 -> compute -> C set 1
DMA reads completed C set 0                    (reserved)
```

Here input set 0 and result set 1 are active. Equal numeric IDs across those
two namespaces do not describe the same storage. This is why admission checks
must use the corresponding input or output owner.

DONE is sticky until an accepted START or reset. It asserts on the edge that
writes the final scheduled microtile row to C memory. `result_valid[output_buf]`
asserts on that same edge. Tail masks prevent writes to padded rows or columns.
Reset clears ownership, responses and counters; BRAM contents are not reset.

## Result storage and readback

Each C buffer contains T-by-T signed INT32 slots, mapped as:

```
bank = column % P
word = buf * (T*T/P) + row * (T/P) + column/P
```

A drain writes P values to distinct banks in one clock. Host reads name a buffer,
row and pair index. Pair q returns columns 2q and 2q+1, with the earlier column
in bits 31:0. For an odd final column, the upper word is zero and the byte strobe
is 0x0f; otherwise it is 0xff. A request outside the completed result's shape,
or to an invalid result buffer, returns `response_error=1`, zero data and strobe.

The read address is accepted on edge 0. Synchronous BRAM data is captured into
the response registers on edge 1. Response valid/data/strobe/error remain stable
until `response_ready` accepts them. There is at most one outstanding read;
the next request can be accepted on the edge after the response is consumed.

## Counters

Counters reset at accepted START and freeze at completion. With no reset:

```
tiles          = ceil(M/P) * ceil(N/P)
job_cycles     = tiles * (K + 3*P + 3)
compute_cycles = tiles * (K + 3*P - 1)
microtiles     = tiles
```

`job_cycles` measures accepted START to final local C write. It includes the
three operand-prefetch edges and one wrapper-launch edge for each microtile.
`compute_cycles` counts the active core schedule only. Neither includes host
loading/readback. These are BRAM-preview counters; the DDR release uses its
separate final-write-response completion contract.

For P4,T32 and M=N=32,K=256, the expected job count is 17,344 cycles.
Simulation and the [September 30 board run](../results/preview/README.md)
match that prediction. At 100 MHz this is 173.44 us of BRAM-local execution;
host upload and readback are separate measurements.

## Checks

`make test-tile` checks both result-bank addressing and complete matrix jobs in
all four P/T configurations. It compares every output with Python integer
matrix multiplication, records every C write to detect duplicates/out-of-bounds
writes, and checks launch order, cycle counts, tails, reset, busy commands and
stalled responses. Padding bytes are deliberately nonzero.

`make test-tile-overlap` checks the opt-in concurrent ports across all four
geometries. It loads the other operand set and reads prior C data while compute
continues, checks mixed input/output IDs, whole-job exclusion, START priority,
pending/stalled responses and reset. Every computed value and completed result
matrix is compared with Python integer dot products. `make mutation-tile-overlap`
requires three deliberately broken private ownership implementations to fail
their expected assertions. The [saved record](../results/tile_overlap_ports/README.md)
also includes fresh default-serial and P8 packet-core regressions.

`make synth-tile` routes the T32 engines with registered neighboring logic at
100 MHz. External harness-pin paths are excluded, while engine and neighboring
register paths are timed with 0.2 ns clock uncertainty. This does not include
UART logic, board I/O, the platform reset network or DDR.
