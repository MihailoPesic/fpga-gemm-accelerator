# Scoped RTL proofs

The proofs use the unchanged production RTL and a pinned Yosys SAT toolchain.
They cover individual interfaces and reduced configurations; they do not prove
the complete accelerator or the physical DDR interface.

| Module | Configuration | Method |
| --- | --- | --- |
| `gemm_dma_rows` | READ_SLOTS=1/4; four 20-byte rows at base 0xff0, stride 64 | 18 assertions through 20 SAT timeframes |
| `gemm_tile_dma_read_queue` | T8/T32; four credits; one-beat non-final descriptors; saved BT/buffer=1 | 37 assertions; reset-based 8-frame base and reset-low induction step |
| `gemm_tile_scheduler` | P4/T8; one prevalidated 1x9x1 job; MODE0/1 | 36/33 assertions through 32 SAT timeframes |

The row-planner harness checks burst metadata, credits and stalled offers.
The queue harness checks occupancy, metadata order and held bank/terminal
payloads. The scheduler harness checks input/result ownership, ordered tags,
serial exclusion, fault retention and completion after both abstract stores.

For the queue, the base query sets reset high at the first timeframe and low
thereafter. The separate induction-only query keeps reset low in every frame;
it cannot repeatedly rely on an initial reset. The saved result proves the
step at induction length 2. Yosys's earlier invariant frames are induction
hypotheses, not additional environmental assumptions.

The queue producer supplies valid bank rows, ordered tags/index0/LAST and
stable stalled offers. Successful responses complete after bank delivery;
sticky stop drains later responses with status7. The scheduler uses abstract
load, store and compute engines: terminals belong to accepted work, compute
reports BUSY before DONE, and each engine accepts one operation at a time.
Readiness, delays and errors remain independent. There is no fairness or
eventual-response assumption. Arbitrary descriptors, multi-beat queue data,
malformed responses, arithmetic, AXI/DDR timing, watchdog and public counters
are outside these new harnesses.

The runner exposes selected existing DUT registers as observation-only output
aliases after lowering memories. It neither changes the production files nor
cuts their drivers. These harnesses are intended for this Yosys flow.

Use a Python environment containing [requirements-formal.txt](../requirements-formal.txt).
The runners never install packages automatically, and reject a different tool
version or an existing output directory.

```text
python scripts/formal_dma_rows.py --build-dir build/formal_dma_rows
python scripts/formal_buffers.py --build-dir build/formal_buffers
python -m unittest tb.test_formal_buffers
```

`--only queue` and `--only scheduler` select a component. Exact source
snapshots, commands, actual exits and independently decoded JSON/VCD witnesses
are retained. Covers demonstrate reachable traffic and stalls; they are
existential examples, not a liveness proof. The final witness sample observes
the last committed transition, so its input offers are not counted as another
executed edge.

Saved evidence: [row planner](../results/dma_rows/formal/README.md) and
[queue/scheduler](../results/buffer_formal/README.md). Trace-validator unit
tests are software checks, not additional RTL proofs.
