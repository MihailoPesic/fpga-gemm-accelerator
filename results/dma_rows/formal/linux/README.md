# Linux row-planner proof run

The unchanged row-planner runner passed on Ubuntu/WSL with Python 3.12.3 and
the pinned Yosys 0.69 toolchain on 4 October 2026. The native wrapper, WSL
process and all ten tool commands exited with code 0. No hardware or system
package changes were involved. The sealed [Windows record](../README.md)
is unchanged.

The two safety queries check all 18 assertions through 20 SAT timeframes,
at READ_SLOTS=1/4, with reset high only in timeframe 1. This is bounded
checking, not induction. The fixed descriptor has base 0xff0, stride 64,
four rows and 20 useful bytes per row. Inputs permit independent request,
burst, completion, cancellation and DONE delays/statuses; initially
unconstrained registers are defined binary values. There is no fairness
or eventual-response assumption, and four-state X behavior is excluded.

The seven independently decoded VCD/JSON witnesses demonstrate complete
five-burst read/write operations with stalls, four read credits,
simultaneous issue/completion and nonzero cancellation followed by later
completion before nonzero DONE. Successful complete operations are
existential witnesses; they are not an additional universal liveness proof.
The scope excludes arbitrary descriptors, AXI data, banks, the scheduler,
the complete accelerator and the DDR PHY.

[summary.json](summary.json) records versions, exact commands, source hashes,
actual tool exits and decoded witnesses. [execution.json](execution.json)
records the actual native/WSL wrapper exits and exact stdin invocation.
[witness_cycles.txt](witness_cycles.txt) provides readable traces.
[manifest.json](manifest.json) independently seals this Linux child record.
All input snapshots, nine query scripts and seven witness pairs are retained.
Ten tool logs plus the native wrapper's stdout are losslessly compressed;
their raw/stored byte counts and SHA-256 hashes are preserved.

Verify every artifact and decompressed log, or extract the exact raw logs:

```text
python results/dma_rows/formal/linux/verify.py
python results/dma_rows/formal/linux/verify.py --extract-logs build/dma_rows_linux_logs
```

Extraction requires a fresh directory inside build/. To reproduce with a
Python environment containing requirements-formal.txt:

```text
python scripts/formal_dma_rows.py --build-dir build/formal_dma_rows_linux
```

The runner never installs packages automatically. Earlier setup/access
failures remain private and are not represented as successful proof runs.
