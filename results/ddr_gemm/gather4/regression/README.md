# C gather optimization regressions

PASS: 60 portable tests across P4/T8, P4/T32, P8/T8 and P8/T32 after removing
the redundant normal-path `NEXT_PAIR` cycle from the tile DMA adapter.
The fault-fill path retains that state. AXI completion, held requests and
byte-strobe obligations are unchanged.

| Boundary | Tests | Completed jobs | Compared outputs | Rejected requests |
| --- | ---: | ---: | ---: | --- |
| [Tile DMA, banks and burst engine](tile_dma/summary.json) | 20 | 108 | 11,039 | 64 invalid transfer descriptors |
| [Serial DDR job controller](ddr_job/summary.json) | 16 | 154 | 86,322 | 124 invalid job descriptors |
| [Framed command subsystem](ddr_core/summary.json) | 24 | 28 | 5,056 | 96 invalid memory commands |

The job-controller suite exercises 1,017 macrotiles. The packet suite completes
3,652 requests and responses, including eight duplicate replays and eight
sequence conflicts; it also rejects 16 malformed frames. Counts include
directed setup jobs as well as randomized cases. The layers reuse some shapes
and are not independent samples of one workload distribution.

## Acceptance-edge fault checks

Three new adapter cases run in every P/T build:

- An unsolicited B response faults the real burst engine on the same edge as
  write-command acceptance. The newly offered C read and its response remain
  held under backpressure and drain before cancellation completes. No AXI write
  or output-memory modification occurs.
- A memory fatal arrives exactly when a previously stalled, nonfinal data word
  is accepted. That word remains unchanged; the suffix uses zero byte strobes,
  no further C reads are issued, and completion waits for the genuine B response.
- A premature successful local completion arrives on that same data-acceptance
  edge. It reports a protocol fault and cannot release the remaining data or B
  obligations.

The existing tests continue to cover signed arithmetic, tails, nonzero padding,
guards, independent channel stalls, malformed responses, watchdogs, calibration
loss, descriptor rejection and frozen counters. The new cases are in
[`gather_transition_fault_edges`](../../../../tb/test_tile_dma.py).

## Reproduction and identity

From an environment with the pinned portable-test dependencies:

```text
python scripts/test_tile_dma.py --build-dir build/check_gather4_tile
python scripts/test_ddr_job.py --build-dir build/check_gather4_job
python scripts/test_ddr_core.py --build-dir build/check_gather4_core
```

The [manifest](manifest.json) hashes all 15 saved artifacts: three original
runner summaries and twelve simulator XML results, copied byte-for-byte.
Each summary records exact source fingerprints, tool versions and coverage.
All recorded inputs were unchanged during their runs and matched the source
files when these results were preserved. XML results contain no failed,
errored or skipped tests. Changed scheduling can alter later seeded stimulus;
the saved coverage records describe the cases actually executed.

This checkpoint verifies the changed source against behavioral AXI RAM. It
does not establish UART pin timing, vendor DDR behavior, routed timing or
performance on the board. The [qualified board baseline](../../board/README.md)
uses the earlier adapter; the C gather optimization requires separate physical
qualification.
