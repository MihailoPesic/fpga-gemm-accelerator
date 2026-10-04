# Tagged macrotile scheduling

Portable checkpoint, 2026-10-04. `gemm_tile_scheduler` connects independent
operand-load, compute and result-store contexts for an already validated
matrix descriptor. Exactly two input sets and two result sets carry separate
tile tags and ownership. MODE=0 schedules serially; internal MODE=1 permits
load, compute and store to progress together.

The [record](record.json) binds source hashes to copied XML, coverage,
summaries and actual process exits. All eight configurations pass: P4/P8,
T8/T32 and one/four outstanding reads. There are **24 passing tests, 112
fully checked jobs, 71,632 useful outputs and 336 retired macrotiles**.

| Configuration | Summary | Result |
| --- | --- | --- |
| P4/T8, READ1/4 | [Summary](p4_t8/summary.json) | 6 tests; 28 jobs; 2,260 outputs |
| P4/T32, READ1/4 | [Summary](p4_t32/summary.json) | 6 tests; 28 jobs; 33,556 outputs |
| P8/T8, READ1/4 | [Summary](p8_t8/summary.json) | 6 tests; 28 jobs; 2,260 outputs |
| P8/T32, READ1/4 | [Summary](p8_t32/summary.json) | 6 tests; 28 jobs; 33,556 outputs |
| Compile checks | [Console](lint_console.txt) | All scheduler geometries and existing production configurations compile |

Three exact cases run in every configuration:

- Full jobs in both modes: five shapes include single-element work, M/N
  tails, multiple row/column tile bands and K=1,9,33,255,256. Each pair
  restores identical guarded A/BT/C memory before running. Every useful
  result, input byte, padding byte and output guard is checked. Addresses,
  burst splits, write strobes, WLAST and response counts are independently
  enumerated. Changing live configuration after acceptance tests snapshotting.
- Held result ownership and completion: a successful store terminal stays
  stalled after B until both result sets fill. A third launch must wait.
  Separate tests retain BUSY after all stores retire while either memory
  boundary is held non-idle. Delayed B itself is covered by the earlier
  [duplex tests](../tile_dma_duplex/README.md).
- Fatal draining: read/write response errors, compute error and external
  fatal stop new work while accepted DMA and compute obligations drain.
  The first code remains frozen and another job remains blocked. Faults
  after prior success clear sticky DONE; a fault sharing the final terminal
  edge cannot produce success.

The owner scoreboard follows public descriptor, compute and completion
handshakes. It checks row-major tag order, prepared inputs, whole-result
reservation and exclusion of live-buffer reuse. A directed held load terminal
lets the first result retire before the next input becomes ready, forcing
different input/result IDs. Every configuration exercises that case and
simultaneous load/compute/store. Across the runs, 32 launches use different
IDs and 20,948 sampled edges have all three scheduler stages active; these
are behavioral coverage counts, not measured FPGA utilization.

The maximum 1024x1024 descriptor checks the admitted tile count: 16,384 at
T8 or 1,024 at T32. It then faults before memory requests. This admission-only
case is excluded from completed-job/output totals and does not establish
maximum-size computation or terminal tag rollover.

The runner rejects missing, duplicate, unexpected, failed or skipped tests,
checks source hashes before/after each configuration and records simulator
and dependency versions. Raw result XML and coverage remain beside each
summary; full simulator logs and generated binaries remain in ignored
`build/`. Reproduce with `make test-tile-scheduler PYTHON=.venv/bin/python`.

Production still uses the serial adapter and rejects MODE=1. The qualified
board image remains `0xd558a543`. This checkpoint adds no UART/register
integration, public job counters, watchdog, formal proof, vendor simulation,
routed timing or board throughput result for overlap. The next integration
must preserve descriptor validation, host exclusion, fatal handling and the
actual final successful B timestamp. See the
[module contract](../../docs/tile-scheduler.md) and
[remaining gates](../../docs/status.md#next-gates).
