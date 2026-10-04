# Serial DDR job controller

[`gemm_ddr_job`](../rtl/control/gemm_ddr_job.sv) sequences complete matrix jobs
through the [tile DMA adapter](tile-dma.md) and
[local engine](tile-engine.md). The arithmetic is signed INT8 multiplication
with signed INT32 outputs, `C = A * B`. DDR stores B transposed as
`BT[j,k] = B[k,j]`; the host is responsible for that layout.

This checkpoint uses one input set, one result set and serial load/compute/store.
P=4/8 and T=8/32 are build parameters. M,N are 1..1024 and K is 1..256.
MODE must be zero; overlap is rejected rather than silently ignored.
The controller exposes a descriptor command interface. The
[register block](ddr-registers.md) translates local bus requests into that
interface. The [packet subsystem](ddr-core.md) adds framed commands and idle
host-memory arbitration. [Platform integration](ddr-board.md) has separate
vendor, routed and physical evidence for P4/T32 and P8/T32. The current
four-cycle C gathering passes each image's 48-job board suite; their
[controlled comparison](../results/ddr_gemm/p8_scaling/README.md) preserves
the same sources, workloads and transfer counts.

## Data flow and tile order

```text
Descriptor START
       |
       v
Snapshot -> Validate -> Serial job controller
                              |           |
                       DMA request     Compute START
                              |           |
                              v           v
DDR2 <-> AXI burst <-> Tile DMA <-> A/BT banks -> Local engine
                         ^                         |
                         +------- C banks <--------+

One macrotile:
load A -> load BT -> compute -> store C -> acknowledge -> next tile
```

The portable harness substitutes behavioral AXI RAM for the vendor platform.
On the board, SmartConnect and MIG occupy the DDR side of the same 64-bit AXI
boundary. Vendor clock conversion and physical DDR behavior are not modeled
by the portable RAM test.

Tile origins visit `(i0,j0)` with columns advancing first:

```text
i0 = 0, T, 2*T, ...
j0 = 0, T, 2*T, ... within each row band
r  = min(T, M-i0)
c  = min(T, N-j0)

A load:  A_BASE  + i0*A_STRIDE         r rows, K bytes per row
BT load: BT_BASE + j0*BT_STRIDE        c rows, K bytes per row
C store: C_BASE  + i0*C_STRIDE + 4*j0  r rows, 4*c bytes per row
```

The complete K dimension stays local. The local engine launches only nonempty
microtiles and masks their unused rows, columns and reduction padding.
The DMA rounds operand reads to eight bytes and writes only useful C bytes.
Both buffer selectors remain zero. A buffer becomes reusable only after the
previous tile's store completes successfully, including its final B response.
Tile addresses advance using stride shifts and fixed column increments;
descriptor allocation multiplication belongs to validation, not each launch.
The controller records whether each origin is in the final row/column band
when accepting the job or advancing a tile. Final write completion uses these
registered flags, keeping dimension arithmetic out of the counter snapshot
enable. Simulation assertions check the flags against the current origins.

## Descriptor validation and commands

The controller snapshots all fields at the START request handshake. Separate
validation stages check dimensions, compute allocation sizes, form widened
ends and check bounds/disjointness before any DMA request.

| Field | Accepted values |
| --- | --- |
| M, N | Unsigned 32-bit values in 1..1024 |
| K | Unsigned 32-bit value in 1..256 |
| Bases | 64-byte aligned, inside the configured DDR window |
| A/BT strides | Multiples of 64, at least K bytes |
| C stride | Multiple of 64, at least 4*N bytes |
| MODE | Exactly zero for this serial checkpoint |
| WATCHDOG | At least one core cycle |

Allocation checks use conservative half-open ranges:

```text
A  = [A_BASE,  A_BASE  + M*A_STRIDE)
BT = [BT_BASE, BT_BASE + N*BT_STRIDE)
C  = [C_BASE,  C_BASE  + M*C_STRIDE)

Each end <= DDR_BYTES; every pair must be disjoint.
Adjacent allocations are allowed. Arithmetic must not wrap at 32 bits.
```

High dimension bits are checked before narrowing to the validated widths.
The default DDR_BYTES is 128 MiB. Actual rounded row accesses are checked
again by the row sequencer.

| Command condition | Response and effect |
| --- | --- |
| Valid idle START | Status 0 after validation; pulse `job_accepted`, snapshot job ID and begin work |
| Invalid descriptor | BAD_DESC (3), no DMA; preserve prior DONE, last accepted ID and counters |
| START while busy | BUSY (4), no queued or duplicate job |
| START without calibration, during host access or after a fatal fault | NOT_READY (5) when idle |
| Idle CLEAR_STATUS | Status 0; clear DONE and recoverable ERROR |
| CLEAR_STATUS during work/validation or coincident START | BUSY (4) |
| Idle CLEAR_STATUS after a fatal fault | NOT_READY (5); reset requirement remains latched |

The START response is held until consumed. Accepted work proceeds independently
of response backpressure. A valid accepted job clears the previous DONE/error
and counter snapshots. Later configuration changes cannot alter that job.
`host_busy` prevents admission; it does not implement host-memory arbitration.

## Completion and counters

`job_accepted` marks validated job acceptance, after descriptor checks.
The controller records the core timestamp of each successful raw AXI B
handshake. Final DMA completion establishes that the last write succeeded;
JOB_CYCLES uses that saved handshake timestamp, excluding the later local
completion propagation. DONE is asserted only after terminal DMA success
and quiescence, with counters already frozen.

```text
JOB_CYCLES = final successful B timestamp - job_accepted timestamp
COMPUTE_CYCLES = sum(local engine compute cycles for completed tiles)
READ_BEATS = accepted AXI R beats
WRITE_BEATS = accepted AXI W beats
WRITE_VALID_BYTES = sum(popcount(WSTRB) for accepted W beats)
```

INPUT_WAIT_CYCLES counts the A/BT request and completion phases.
READ_STALL_CYCLES counts RVALID without RREADY; WRITE_STALL_CYCLES counts
WVALID without WREADY. These stalls do not alone measure command latency.
All counters exclude idle host memory transfers and freeze at completion.
There is no live software counter snapshot in this interface.

With T divisible by P and even, complete serial jobs also satisfy:

```text
READ_BEATS = ceil(K/8) * (M*ceil(N/T) + N*ceil(M/T))
WRITE_BEATS = M*ceil(N/2)
WRITE_VALID_BYTES = 4*M*N
COMPUTE_CYCLES = ceil(M/P)*ceil(N/P)*(K+3*P-1)
```

A rows are reloaded once per output column band; BT rows are reloaded once
per output row band. These identities check traffic and compute work without
reproducing the controller's state transitions in the scoreboard.

## Faults and reset

Memory-response errors (7), protocol faults (8), watchdog expiry (9) and
calibration loss (10) latch ERROR and RESET_REQUIRED. The first fatal event
freezes the public counters at the fault timestamp. Later draining cannot
change that snapshot or produce DONE. Partially written C is invalid.
The fault compute snapshot adds the active tile's pre-edge core counter to
the completed tiles. A concurrent core update on that fault edge is excluded;
AXI handshakes on the fault edge are included. Later drain work is excluded.

Before calibration has first succeeded, a low DDR_READY only blocks START.
After it has been observed high, any loss requires coordinated reset, even
while idle. Idle loss clears DONE and preserves the prior counters/job ID;
loss during descriptor validation rejects the pending START without DMA.
Calibration returning high cannot silently restore job admission.

The no-progress watchdog resets on accepted DMA work, actual memory progress
or compute progress. Polling and stalled commands do not reset it. An already
running local computation can finish; unstarted tiles and new DMA work stop.
START snapshots both the limit and `limit-1`; validation rejects a zero limit.
On an active edge without progress, the pre-edge count reaching `limit-1`
triggers the fault. Preparing that threshold during START preserves the fault
edge while removing subtraction from the active comparison path.

```text
Tile DMA.mem_fatal = AXI burst.fatal OR host_control.host_fatal
                    OR job.RESET_REQUIRED
job observes Tile DMA.fatal
host_control.mem_fatal = AXI burst.fatal OR job.RESET_REQUIRED
```

This is the integrated [packet core](../rtl/control/gemm_ddr_core.sv) wiring.
The returning burst, host-control and job fault signals are registered;
the host controller handles its own fatal latch separately from its incoming
memory-fault signal. Combinational fault feedback must not replace this wiring.
The first active-job fault freezes its public counters; subsequent host or
memory drain activity cannot change that snapshot. The adapter retains
already offered bank requests and drains accepted burst obligations. The
controller consumes DMA completion and waits for DMA/core idle and AXI
quiescence before clearing BUSY. If the memory interface remains hung, ERROR
is visible while BUSY stays asserted until coordinated platform reset.
Watchdog expiry never permits withdrawal of an asserted AXI VALID.

Reset clears controller/engine/DMA ownership and protocol state together;
resetting this controller alone during outstanding traffic is unsupported.
The currently qualified board procedure remains cold power-up in JTAG mode;
see [reset qualification](ddr-diagnostic.md#reset-qualification-remains-open).

## Verification boundary

`make test-ddr-job PYTHON=.venv/bin/python` connects the production controller,
DMA adapter, burst engine, banks and compute engine to behavioral AXI RAM.
The oracle uses mathematical matrices and Python integer dot products.
The [C gather regression](../results/ddr_gemm/gather4/regression/README.md)
passes 16 job-controller tests across all four P/T builds: 154 jobs, 86,322
compared outputs, 124 invalid descriptors and 1,017 macrotiles. Exact executed
shapes and source fingerprints are saved; shared random stimulus means changed
RTL latency can change later seeded cases. The
[pipeline regression](../results/ddr_job/pipeline/README.md) and
[original checkpoint](../results/ddr_job/README.md) retain their earlier evidence.
This portable check is separate from vendor integration, routed timing,
physical DDR GEMM and the overlap/concurrent-read release.
