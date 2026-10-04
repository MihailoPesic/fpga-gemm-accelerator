# Linux formal run

The pinned public runner completed all 21 queries on Ubuntu through WSL on
4 October 2026, using Python 3.12.3 and Yosys 0.69 (git 9f75ca1f9).
All 22 tool commands, five WSL stages and the observed native wrapper exit
were successful. The eight source inputs remained unchanged. All 15 saved
JSON/VCD witness pairs pass independent traffic replay.

| Check | Configuration | Result |
| --- | --- | --- |
| Read queue safety | T8 and T32, 37 assertions each | Eight-frame reset base and reset-low induction, proven step length 2 |
| Read queue covers | Both configurations | Full credits, pointer wrap, independent stalls and stop followed by drain |
| Scheduler safety | P4/T8, M1/N9/K1, MODE0 and MODE1 | 36/33 assertions through 32 SAT timeframes |
| Scheduler covers | Same fixed two-tile descriptor | Success, held offers and fatal drain; MODE1 also reaches distinct-buffer load/compute overlap |

This repeats the [native proof scope](../README.md) on Linux. Queue descriptors
are one-beat and non-final, with legal ordered responses. The scheduler uses
abstract legal DMA/compute terminals and a fixed descriptor. There is no
fairness assumption; covers demonstrate reachable behavior, not eventual
completion for every stall schedule. Scheduler safety is bounded, not
inductive. Arithmetic, arbitrary descriptors, malformed multi-beat responses,
physical AXI/DDR behavior, watchdog and public counters are outside this proof.
This archive is software evidence, not board or Vivado qualification.

[summary.json](summary.json) retains actual commands, tool versions, assumptions,
assertion counts and witness results. [execution.json](execution.json) binds
the five actual WSL exits and original output hashes. The execution/ directory
retains exact stage scripts, stdout/stderr, the execution wrapper snapshot and
the observed native completion receipt. Earlier failed setup attempts remain
private. [witness_cycles.txt](witness_cycles.txt) contains compact cycle tables.

[manifest.json](manifest.json) seals every archived byte, including the eight
input snapshots, 21 query scripts and 15 witness pairs. All 22 tool logs use
deterministic lossless gzip; their stored and raw hashes are recorded. The
verifier also checks receipts, assertion imports and replays the witnesses
using the archived decoder, without requiring Yosys or optional packages.

```text
python results/buffer_formal/linux/verify.py
python results/buffer_formal/linux/verify.py --extract-logs build/buffer_formal_linux_logs
```

Log extraction requires a fresh directory inside the repository's build/
directory. Reproduce the queries with the pinned [formal setup](../../../formal/README.md):

```text
python scripts/formal_buffers.py --build-dir build/formal_buffers_linux_reproduce
```
