# DMA row sequencing

The current [registered validation regression](pipeline/README.md) passes
four tests: 271 valid and 121 invalid descriptor cases, with 4,607 accepted
bursts checked. It adds reset coverage in each validation stage and combined
maximum-width invalid fields. The multiplication, footprint addition and
validation now occupy separate clock cycles; command semantics are unchanged.

The original pre-pipeline result below remains archived with its own source
hashes. Its counts describe that revision.

The serial row sequencer passes four standalone portable tests and the Icarus
compile checks on 2026-10-02. This recorded run generates command metadata and
waits for completion; it carries no matrix data and contains no AXI or banks.
The [contract](../../docs/dma-rows.md) defines that boundary.

| Check | Recorded result |
| --- | ---: |
| Cocotb tests | 4 passed, 0 failed or skipped |
| Valid / invalid descriptor cases | 265 / 120 |
| Seeded random valid / invalid cases | 200 / 100 |
| Accepted burst commands checked | 4,601 |
| Burst lengths exercised | Every length 1..16 |
| Stalled burst / completion-result cycles | 9,153 / 1,146 |
| Delayed downstream completion cycles | 11,495 |
| Error completion coverage | Codes 7 and 8 at first, middle and final bursts, for reads and writes |
| Local reset states | Idle, offered command, waiting for completion, held result |

The independent Python oracle first enumerates each row's word addresses and
useful byte masks, then groups them by page and burst limit. It compares every
accepted command's address, count, row/word index, last flags and final-byte
mask. It does not use the RTL next-state equations. The handshake scoreboard
checks request snapshotting, stable stalled payloads, one outstanding command,
no command after an error and no successful DONE before the final completion.

Directed cases include a 256-byte row starting at `0x0ff8`, which splits into
1, 16 and 15 beats; the highest legal DDR word; widened overflow cases; maximum
row counts and row lengths; and odd INT32 output tails. The simulator tests the
default 128 MiB window. Other parameter values have not been qualified.

See [summary](summary.json), [coverage](coverage.json),
[original test results](results.xml) and [console excerpt](console_excerpt.txt).
The summary retains the five tested source hashes and deterministic workload
seeds. Saved text uses LF; original local-artifact hashes remain separately
identified. The complete local console stays under `build/` with its hash
recorded here. The [test source](../../tb/test_dma_rows.py) and
[runner](../../scripts/test_dma_rows.py) reproduce this result:

```text
make test-dma PYTHON=.venv/bin/python
```

The [tile DMA regression](../tile_dma/README.md) tests the sequencer connected
to the production bank and AXI interfaces; each saved revision identifies its
tested source hashes.

Compile checks are not routed timing or full static lint. Existing packet
transport compilation emits Icarus's constant-select sensitivity limitation;
the new sequencer compiles without warnings. This unit has no synthesis,
routed timing, data-movement, AXI integration or physical-board result yet.
Local reset testing does not authorize resetting an outstanding AXI master.
