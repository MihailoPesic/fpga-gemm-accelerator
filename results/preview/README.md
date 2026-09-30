# BRAM preview verification - 2026-09-30

The P4,T32 preview completed 180 physical-board jobs with all 90,510 result
values matching the integer oracle. The complete top passes routed timing at
100 MHz. This release uses local BRAM and 115200-baud UART.

## Board measurements

The six cases below each ran 30 times. Every job downloaded and compared every
output; all passed with zero transport retries or rejected response frames.
Inputs were packed/uploaded once per case and reused for the remaining 29 jobs.
The seed is 20260930; the extreme cases use -128 operands. Raw measurements are
saved as [JSON](hardware/results.json) and [CSV](hardware/results.csv).

| Case | M,N,K | Job cycles, min = median = max | Local job time | Useful local GOPS |
| --- | --- | ---: | ---: | ---: |
| Signed extreme | 1,1,1 | 16 | 0.16 us | 0.0125 |
| Odd shape | 3,5,7 | 44 | 0.44 us | 0.4773 |
| Non-square | 9,6,33 | 288 | 2.88 us | 1.2375 |
| Tile edge | 31,29,255 | 17,280 | 172.80 us | 2.6533 |
| Maximum random | 32,32,256 | 17,344 | 173.44 us | 3.0229 |
| Maximum extreme | 32,32,256 | 17,344 | 173.44 us | 3.0229 |

Job counters cover accepted START through the final local C write, including
operand prefetch, array fill/drain and controller overhead. Useful GOPS is
`2*M*N*K*100000000/job_cycles/1e9`; it excludes UART loading and result transfer.
It is a BRAM-resident job measurement, not a DDR or host-throughput result.

For the maximum random case, the first host-inclusive job took 3.777 s,
including packing/upload/configuration. The next 29 jobs reused BRAM inputs
and had a median host-inclusive time of 0.631 s. Both include command/status
traffic, counter reads and output download; Python validation is recorded
separately. The [manifest](manifest.json) preserves per-case minimum, median
and maximum measurements and the source/build identity. This run does not
establish 1-Mbaud operation or the planned 30-minute endurance result.

## Simulation and host tests

`make lint test-preview PYTHON=.venv/bin/python` passes with Icarus 12.0 and
cocotb 2.0.1 under Ubuntu/WSL. The [manifest](manifest.json) records current
source hashes, suite evidence and the identities of the local test logs.

| Layer | Result and exercised behavior |
| --- | --- |
| Python host | 20 tests: independent packing/oracle, binary framing, retries, timeouts, reset/reconnect, identity checks and complete comparisons |
| Packet transport | 57 backend requests, 66 replies, 17 malformed frames, five resets, 4,431 stalled TX cycles and 1,168 stalled request cycles |
| Preview controller | 21 complete jobs, 5,580 output comparisons, 157 rejected commands, five resets and two injected internal protocol faults |
| UART integration | 38 commands over asynchronous 8N1 pins, one 3-by-5 output matrix with all 15 values checked, two corrupt frames and two resets |

The controller suite includes 32-by-32 matrices with K=256, signed extrema,
odd/non-square shapes, whole-command validation before writes, busy rejection,
counter snapshots and 3,284 stalled response cycles. Injected protocol faults
must suppress successful completion and result access, preserve the previous
successful counters and require reset while diagnostic reads remain available.

Transport cases include an active duplicate, same-sequence conflicts, maximum
payloads, malformed COBS/CRC/lengths, RX collector release boundaries and two
oversized backend responses. Per-suite coverage, XML results and source-hash
summaries are in [simulation](simulation/). Coverage counts describe exercised
cases; they are not exhaustive protocol verification.

UART simulation uses a 100 MHz clock and 5 Mbaud to reduce test runtime. That
simulation setting is not a board baud-rate measurement. Host unit tests use
a serial-interface fake; the separate UART test exercises the RTL pin interface.

An earlier `make lint test mutation` run also passed the core, operand-memory
and local-engine suites and caught all three core mutations: unsigned multiply,
product validity surviving clear, and early drain. Those functional RTL files
are unchanged. Subsequent transport changes were checked by the final focused
run above. The [mutation results](simulation/core_mutations.json) concern the
compute core; they do not establish DMA or scheduler fault coverage.

Icarus reports its known `always_*` sensitivity limitation for constant selects
and includes all bits in the sensitivity set. Compilation and all suites pass.
No full console logs, simulator binaries or waveforms are included here.

## Board implementation

The complete P4,T32 top passes routing at 100 MHz on `xc7a50ticsg324-1L`,
using Vivado 2026.1 and 0.200 ns user clock uncertainty. This build uses
115200-baud UART. [build.json](physical/build.json) records the build ID and
bitstream hash. Its 16 hashed inputs were verified against
[commit 3b9402a](https://github.com/MihailoPesic/nexys-accelerator/commit/3b9402abe0b20f73e0b46356452d5ee99cc537dc)
after the build; the original checkout/dirty fields are retained.

| Check | Routed result |
| --- | ---: |
| Setup / hold slack | +0.188 / +0.005 ns |
| Pulse-width slack | +3.750 ns |
| LUTs / fabric registers | 9,166 / 6,387 |
| DSP48E1 | 16 |
| RAMB36E1 / RAMB18E1 | 8 / 4: 10 RAMB36 equivalents |
| Unconstrained internal endpoints / routing errors | 0 / 0 |

The worst setup path is reset distribution to a packet-payload register:
8.874 ns data delay, of which 8.455 ns is routing. The worst hold path connects
the operand prefetch next/current registers. Both margins are positive but
small; changes to the implementation require another timing check.

[CDC](physical/cdc.txt) recognizes the UART RX and reset-button inputs as
three-stage single-bit synchronizers, both informational findings. Reset
assertion and release are synchronous to the free-running core clock, keeping
BRAM address/enable controls timed. The saved checks contain no critical
warnings or errors. Remaining advisories are eight SYNTH-5 distributed-memory
mapping warnings, twelve SYNTH-6 BRAM output-register warnings and two DPIP-1
DSP input-pipeline warnings. They identify potential implementation
improvements; the routed target passes with the current pipeline.

The [initial board run](physical/initial/timing.txt) failed setup at -1.596 ns,
with 13,484 LUTs and 10,432 fabric registers. Transport memory/CRC restructuring
and synchronous reset reduced the final resource counts and enabled this
passing implementation while retaining the P4 compute engine. Several changes
contributed; this is an engineering iteration, not a measured single-factor
speedup. The initial timing and utilization reports remain available.

These implementation reports establish physical timing and build identity;
the hardware dataset above records execution and complete output comparisons.
