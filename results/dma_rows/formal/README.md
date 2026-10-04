# Bounded row-planner checks

The public runner passes two safety queries and seven reachability queries
for `gemm_dma_rows`, with READ_SLOTS=1/4. Each safety query imports all 18
assertions through **20 SAT timeframes**, including the first synchronous-reset
transition. This is bounded checking, with no induction or fairness assumption.

The fixed descriptor has base `0x0ff0`, stride 64, four rows and 20 useful bytes
per row. It exercises a 4 KiB split and a final `0x0f` write mask. Independent
input signals allow arbitrary request, offer, completion, cancellation and
DONE delays/statuses. Initially unconstrained registers have defined binary
values; four-state X behavior is outside the scope.

Universal assertions check stalled offer/DONE stability, outstanding-credit
bounds, no completion without owned traffic, no DONE with accepted commands
outstanding, and exact burst metadata for this descriptor. An unaccepted
queued-read offer may withdraw on cancellation or completion error; this
local command interface is separate from AXI VALID obligations.

The seven existential witnesses show successful read/write completion at each
depth with five issued/completed commands and real offer/DONE stalls, four
read credits, simultaneous issue/completion, and nonzero cancellation followed
by later completion before nonzero DONE. The Python decoder independently
reconciles public handshakes, metadata and credits, and checks VCD against JSON.
Successful complete operations are checked in these witnesses; they are not
an additional universal proof of successful completion or first-error priority.

[summary.json](summary.json) records assumptions, tool versions, every actual
tool exit, assertion imports, source hashes and decoded witnesses. Exact input
copies, nine SAT scripts, ten logs and seven VCD/JSON pairs are preserved.
[witness_cycles.txt](witness_cycles.txt) provides compact readable traces.
The cover logs intentionally show a countermodel to `cover_signal == 0`;
that model demonstrates reachability, and `-falsify` requires it to exist.

This result does not prove arbitrary descriptors, request/BUSY exclusivity,
first-error preservation, a FIFO, buffer ownership, AXI, the scheduler, the
complete accelerator or the physical DDR interface. Reproduce the exact run
with the [formal setup](../../../docs/testing.md#bounded-formal-checks).
