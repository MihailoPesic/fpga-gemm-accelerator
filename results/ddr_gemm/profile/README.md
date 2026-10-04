# Serial DDR cycle attribution

The production portable RTL passes 16 profiled jobs across P4/T8 and P4/T32
with two behavioral AXI RAM models. All 25,156 outputs and 384,000 complete
allocation/guard bytes are checked. The four distinct workloads are
32x32x256, 5x3x9, 33x35x256 and 65x63x255; each runs in all four configurations.
Inputs, padding, guards, addresses and row strides match the corresponding
board cases. The core clock is 100 MHz and MODE is serial throughout.

These results explain the unchanged RTL's schedule under stated memory
responses. They do not measure physical DDR latency or establish a new
bitstream performance result. The existing
[48-job board qualification](../board/README.md) retains its original identity.

## Dense job accounting

The unstalled P4/T32 model takes 25,648 cycles for 32x32x256:

```text
Phase                                    Cycles
A load                                    2,515
BT load                                   2,515
Local compute                            17,346
  active microtile schedules             17,088
  launch/prefetch/completion overhead        258
C store                                   3,272
                                        -------
JOB_CYCLES                               25,648
```

Only the four phase totals are additive; the indented compute entries
partition the compute phase. The counter ends at the final successful AXI B
handshake. DONE appears three clocks later, outside the job interval.

Each burst is checked against an independently enumerated address plan.
The guarded dense layout produces 132 reads and 33 writes, including 4 KiB
splits: 124 read bursts of 16 beats and eight of eight beats; 31 write bursts
of 16 beats and two of eight beats. Total traffic remains 2,048 read beats
and 512 write beats.

Reads finish buffering before local delivery begins. After the final R beat,
an L-beat burst takes L+1 cycles to deposit its final operand word in the
banks. There is one read burst outstanding. C gathering spends 2,560 cycles:
512 preparing pairs, 512 admitting reads, 1,024 awaiting synchronous
responses and 512 collecting words. This is five clocks per 64-bit pair.
See the [module schedule](../../../docs/tile-dma.md#serial-schedule).

## Controlled reuse comparison

All entries below use the same dense matrices, DDR layout, P4 and clock.
They are behavioral-model measurements:

| Quantity | T8 | T32 |
| --- | ---: | ---: |
| Macrotiles | 16 | 1 |
| Active compute cycles | 17,088 | 17,088 |
| Input bytes | 65,536 | 16,384 |
| Result bytes transferred | 4,096 | 4,096 |
| Read bursts | 528 | 132 |
| Write bursts | 128 | 33 |
| Unstalled job cycles | 41,548 | 25,648 |
| Delayed-model job cycles | 46,540 | 26,902 |

T32 reduces input traffic fourfold and total traffic from 69,632 to 20,480
bytes, a 3.4-fold reduction. Useful computation is identical; macrotile
control and burst counts change with the reuse boundary. These numbers
are not presented as a measured board speedup.

## Validate the measurement

Random AXI channel pauses are disabled. The unstalled RAM returns its first
read beat two cycles after AR and B two cycles after both AW and final W.
The delayed model adds eight cycles before each burst's first read word and
six cycles before its final write word is committed and B can be produced.
Callbacks retain their post-edge queuing phase. The runner requires each
affected burst interval to grow by exactly the stated amount and all other
burst intervals to remain unchanged.

```text
added job cycles = 8 * read bursts + 6 * write bursts
T32 dense:       = 8 * 132 + 6 * 33 = 1,254
26,902 - 25,648  = 1,254
```

All eight workload/model comparisons pass this identity. Phase totals match
JOB_CYCLES; input phases match INPUT_WAIT_CYCLES; active schedules match
COMPUTE_CYCLES. Raw accepted beats, byte strobes and data stalls match their
frozen counters. The additional pre-edge monitor agrees with the existing
packet regression's AXI handshake log. Full output, input, padding and guard
checks precede PASS.

## Interpret the board comparison

[Physical job 19](../board/07_resident_dense/results.json) uses the same dense
layout and records 34,635 job cycles, 13,176 input-wait cycles and 17,088
active compute cycles. Against the unstalled model, the job differs by
8,987 cycles, with 8,146 of that difference in input phases. This locates
about 90.6% of that record's additional cost in loading; it does not separate
MIG command latency, clock/width conversion, refresh or gaps between R beats.
Zero read-stall cycles only means offered data were accepted.

The next bounded change is to remove the pair-preparation bubble while
preserving stalled requests and fatal-drain behavior. The predicted dense
collection cost is 2,048 cycles instead of 2,560, saving 512 clocks. That
prediction requires fresh regressions, vendor simulation, routed timing and
the same board workloads before it becomes a physical improvement claim.
Larger read gains require queued commands and additional reserved buffering
while preserving complete-burst validation.

## Reproduce and inspect

```sh
make profile-ddr PYTHON=.venv/bin/python
```

The [accounting convention](../../../docs/ddr-core.md#cycle-attribution)
defines sampling and completion boundaries. [cycles.csv](cycles.csv) contains
all 16 job records. Each configuration directory contains a compact job
summary, burst CSV and unchanged passing test XML. Burst tables retain
event endpoints, counts and gap histograms; they are derived from the full
ordered local traces, whose original hashes are recorded.

[compact_summary.json](compact_summary.json) records versions, the controlled
comparisons, 20 source fingerprints and artifact hashes.
[manifest.json](manifest.json) binds the saved copies to the completed run.
The original dirty base commit is retained; hashes identify the tested files.
Complete traces and simulator build products remain under ignored `build/`.
No production RTL, UART/platform source or bitstream changed during profiling.
