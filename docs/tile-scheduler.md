# Tagged macrotile scheduler

`rtl/control/gemm_tile_scheduler.sv` schedules a validated matrix descriptor
over the [duplex DMA](tile-dma.md#independent-load-and-store-contexts) and
[concurrent local engine](tile-engine.md). The current selectable DDR build
integrates this internal module under `gemm_ddr_overlap_job`, with UART,
registers and MODE=0/1 support. The separate serial build uses
`gemm_ddr_job` and rejects MODE=1. See [overlap integration](ddr-overlap.md)
for the public job contract and qualified images.

## Interface boundary

`job_valid/job_ready` snapshots dimensions, DDR bases/strides and mode. The
caller must validate the complete descriptor and allocations before this
handshake: M,N=1..1024, K=1..256, mode=0/1, legal aligned strides/bases,
disjoint regions inside DDR. Simulation assertions catch invalid basic
fields; they are not a hardware descriptor validator.

The surrounding job controller supplies calibration/host exclusion,
watchdog, job IDs, status/error registers and frozen performance counters.
It must connect the scheduler's registered fatal output to DMA stop feedback
and return the DMA's aggregate fatal through `mem_fatal`. Replacing that
registered feedback with combinational stop would create a fatal-signal loop.

Three independent interfaces operate in the core clock domain:

```text
validated job --> scheduler
                   |
                   +--> load_req / load_done ---> A/BT reader
                   +--> compute_start / done --> local array engine
                   +--> store_req / store_done -> C writer
```

Compute START is a pulse emitted only when the local engine is ready. The
scheduler reserves and holds its input/output metadata while waiting for
that edge. DMA descriptors use ordinary valid/ready and remain stable under
backpressure, except that an unaccepted local request can be canceled on a
fatal fault. Accepted DMA operations still require their terminal handshake.

## Tile identity and traversal

The ingress cursor visits row origins 0,T,2T,... and column origins
0,T,2T,... within each row band. Every tile receives a monotonically increasing
tag and captures its origins, useful rows/columns and A/BT/C addresses.

```text
rows = min(T, M-i0)       columns = min(T, N-j0)
A tile base  = A_BASE  + i0*A_STRIDE
BT tile base = BT_BASE + j0*BT_STRIDE
C tile base  = C_BASE  + i0*C_STRIDE + 4*j0

tile count = ceil(M/T) * ceil(N/T)
```

Addresses advance by constant T-row stride steps or 4*T output-column bytes.
The full K dimension is loaded for each tile; no external partial sums are
introduced. T8 at the largest descriptor has 16,384 tiles. Per-buffer tags
have 15 bits, and admission/compute/retirement cursors have 16 bits so the
terminal count also fits.

## Separate buffer lifetimes

Exactly two operand sets and two result sets exist. Input IDs and result
IDs are separate namespaces; output selection does not follow input parity.
Each active buffer carries its tile tag and captured metadata.

```text
Input:  FREE -> FILLING -> READY -> COMPUTING -> FREE
                   A, then BT       |
                                    +-- qualified compute completion

Result: FREE -> COMPUTING -> READY -> WRITING -> FREE
                  reserve                        |
                                                 +-- store DONE handshake
                                                     after final tile B
```

Loading A and BT uses one reader context. Only both successful operation
completions make the input READY. Compute selects the READY input matching
the next compute tag and reserves a whole FREE result set before START.
Its input stays owned throughout all microtiles. The result becomes READY
only after that launch has been observed busy and then completed; an old
sticky compute DONE cannot retire a new launch.

The writer selects the READY result matching the next retirement tag.
Result ownership remains WRITING through the DMA operation's terminal
handshake, including held DONE after B. A slow writer can fill both result
sets and prevent another launch. It cannot overwrite either result or pause
an already launched microtile. Store progress does not depend on loading
the next input.

```text
Possible overlap:

stage       tile t-1          tile t          tile t+1
load                                          A/BT
compute                       array
store       C -> DDR

Each stage advances its own cursor when its consumers have finished.
```

MODE=0 admits another tile only after the preceding tile retires, keeping
load/compute/store serial. MODE=1 admits another tile when an input set is
free, independently of completed-result writes. Both modes keep row-major
compute and store order and use identical mathematical layouts. Serial mode
in this new scheduler is a functional baseline; its control cycles are not
claimed identical to the existing qualified serial controller.

## Completion and faults

Scheduler DONE requires all tiles admitted, computed and retired; both DMA
contexts idle; no active compute; all normal buffer owners FREE; and both
burst `local_idle` and external `axi_quiescent`. Final store retirement is
recorded first, then those conditions are checked on a later edge. DONE is
sticky until a new accepted job, fatal fault or coordinated reset.

The enclosing controller must retain the timestamp of the final successful
AXI B handshake for JOB_CYCLES. Scheduler DONE occurs later because it waits
for local operation drain; using its edge as the kernel endpoint would
change the public counter contract.

Memory fatal, compute error, nonzero DMA completion or an unexpected terminal
stops new requests/launches immediately. The first observed error is captured
and held until reset; codes outside 7..10 normalize to PROTOCOL=8. A fatal
clears DONE, including a fault after an earlier successful job.

Accepted load/store operations and compute continue draining. Pending
unaccepted offers are canceled. Ready/filling/completed ownership is
invalidated only after accepted contexts and both memory boundaries are
idle. The scheduler can then leave BUSY, but fatal keeps job admission
disabled until coordinated reset. Partially written C is invalid.

## Verification boundary

`make test-tile-scheduler PYTHON=.venv/bin/python` uses the real banked array,
duplex DMA and buffered AXI engine with a behavioral AXI RAM. The scoreboard
assigns tile owners from accepted A/BT, compute and store handshakes, checks
ordered retirement and excludes live-buffer reuse. It derives addresses and
useful data from an independent full-matrix oracle.

Cases restore exactly the same A/B bytes for both modes, check all outputs,
input padding and output guards, hold a store operation completion until
both result sets fill, and test faults at active and final-completion
boundaries. The maximum descriptor test checks only admitted tile-count
width and then faults before issuing memory work; it is not a completed
1024x1024x256 comparison or proof of terminal tag rollover.

The [saved checkpoint](../results/tile_scheduler/README.md) passes all eight
P/T/read-depth configurations: 24 tests, 112 complete jobs and 71,632 outputs.
Every configuration exercises different input/result IDs and simultaneous
load/compute/store. Actual zero process exits, raw XML/coverage and unchanged
source identities are preserved separately from earlier board evidence.

That standalone checkpoint establishes the scheduler boundary, rather than
the complete public job contract. The current [integrated overlap build](ddr-overlap.md)
adds descriptor/register/UART integration, counters and watchdog. Its
[verification summary](verification.md) links the separate scoped proofs,
vendor simulation, routed timing and board measurements; those results
retain their own configurations and evidence boundaries.
