# Registered row validation

The current row sequencer passes all four standalone tests after separating
descriptor validation into registered product, footprint and comparison stages.
The first burst is offered three edges after request acceptance. Burst addresses,
masks, completion ordering and rejection rules retain the same contract.

| Check | Recorded result |
| --- | ---: |
| Tests | 4 passed, 0 failed or skipped |
| Valid / invalid descriptor cases | 271 / 121 |
| Included random valid / invalid cases | 200 / 100 |
| Accepted bursts | 4,607; all lengths 1..16 |
| Stalled burst / result cycles | 9,164 / 1,158 |
| Delayed completion cycles | 11,505 |
| Local reset states | All seven, including each validation stage |

The independent oracle enumerates row word addresses and useful byte masks,
groups them by 4 KiB page, then partitions each group into at most 16 words.
Checks cover exact DDR-end acceptance, widened overflow rejection, held
payloads, request snapshots, one outstanding burst and errors at the first,
middle and final burst. Added cases reset each validation stage and reject a
combined maximum-field descriptor without issuing a command. Every reset case
is followed by a fresh successful operation.

[Summary](summary.json), [coverage](coverage.json), [test results](results.xml)
and the [console excerpt](console_excerpt.txt) record the run. The
[manifest](manifest.json) identifies all five tested source files, exact saved
artifact bytes and the complete local console. Compilation uses Icarus `-Wall`;
this is not a full static-lint or physical timing result.

```text
.venv/bin/python scripts/test_dma_rows.py --build-dir build/test_dma_rows_pipeline
```

These results cover the current validation pipeline. The
[original row evidence](../README.md) and earlier integration snapshots remain
unchanged and refer to their own source hashes. This standalone run tests the
default 128 MiB window and local interfaces; it does not establish timing
closure, AXI integration or board operation for the modified RTL. Local reset
testing does not authorize resetting a master with outstanding AXI obligations.
