# FIFO and reduced scheduler proofs

The pinned Yosys 0.69 run completed 21 proof/cover queries on 4 October 2026.
Every actual tool exit is 0. Production RTL was unchanged; the input snapshots
and current source hashes match. This result combines an inductive queue proof
with a bounded scheduler check. It is not a proof of the full accelerator.

| Configuration | Safety | Reachable witnesses |
| --- | --- | --- |
| Read queue T8 | 37 assertions; reset-based 8-frame base and 2-step induction | Four credits, five retirements across pointer wrap, bank/terminal stalls, stop then later drain |
| Read queue T32 | Same 37 assertions and method | Same 4 covers |
| Scheduler P4/T8, MODE0 | 36 assertions through 32 SAT timeframes | Complete two-tile job, held load/store offers, fault then later drain |
| Scheduler P4/T8, MODE1 | 33 assertions through 32 SAT timeframes | Same 3 covers plus simultaneous load/compute in distinct input buffers |

Queue assertions check occupancy bounds, head/tail and metadata order, held
bank-word/terminal payload stability and accepted-work retention. The producer
supplies **one-beat, non-final descriptors**, valid bank rows, ordered response
tags/index0/LAST and stable stalled offers. Address, word and 64-bit data values
are symbolic; saved BT and buffer ID are fixed to 1. Command, bank and terminal
backpressure are independent. Sticky stop drains later responses with status7.

The queue's eight-timeframe base sets reset high only in timeframe 1. Its
**separate induction-only query keeps reset low in every frame** and proves
the step at length 2. Earlier invariant frames are induction hypotheses.
There is no reset-high constraint inside the induction window.

Scheduler assertions check ownership exclusion, no early buffer release,
ordered tags, serial exclusion, stable stalled load/store descriptors,
first-fault retention and success only after both abstract stores finish.
The fixed descriptor is **M1/N9/K1, P4/T8**, bases 0x1000/0x2000/0x3000,
strides 64, in each mode. Abstract engines accept one operation each; terminals
belong to accepted work and compute reports BUSY before DONE. Readiness and
completion delays are independently symbolic; DMA statuses are 0/7, memory
fatal code 7 and compute-error code 8. Reset is high only in timeframe 1 for
the scheduler safety and all cover queries. This is bounded checking, not
scheduler induction.

There is no fairness or eventual-response assumption. Covers are existential
witnesses, not universal successful-completion guarantees. All 15 VCD/JSON
pairs are cross-checked and public traffic is independently replayed. The
final sample observes the last committed transition; its input offers are
not counted as another executed edge.

The runner adds observation-only output aliases to existing DUT registers
after lowering memories. No driver is cut or replaced. Arbitrary descriptors,
multi-beat/malformed queue responses, matrix arithmetic, real AXI B timing,
watchdog, public counters and the DDR PHY are outside this result.

[summary.json](summary.json) records exact configurations, assumptions,
assertion imports, versions, source hashes, commands and decoded witnesses.
[execution.json](execution.json) binds the actual native runner exit.
[witness_cycles.txt](witness_cycles.txt) contains readable cycle tables.
[manifest.json](manifest.json) seals the archive. Input snapshots and every
query script are retained. All 22 tool logs are losslessly gzip-compressed;
the manifest records both stored hashes and exact raw byte counts/hashes.
Earlier failed tool/preparation attempts remain private.

Verify every artifact and each decompressed log with:

```text
python results/buffer_formal/verify.py
python results/buffer_formal/verify.py --extract-logs build/buffer_formal_logs
```

The optional extraction directory must be fresh and inside build/. Reproduce
the proofs in a fresh directory using the [formal setup](../../formal/README.md):

```text
python scripts/formal_buffers.py --build-dir build/formal_buffers
```

The 10 trace-validation software tests also pass normally and under Python -O.
They reject corrupt/missing traces, counterfeit cover flags and unready
compute starts; they are not additional RTL proofs.
