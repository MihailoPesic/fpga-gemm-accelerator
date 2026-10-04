# DDR command and memory subsystem

[`gemm_ddr_core`](../rtl/control/gemm_ddr_core.sv) connects packet transport,
the local register bank, descriptor controller, tile DMA, compute engine and
one shared AXI burst engine. Its input/output bytes are the boundary to a UART
PHY. The board wrapper supplies calibrated status, coordinated reset and the
SmartConnect/MIG path; behavioral AXI RAM supplies memory in portable tests.

The default serial configuration reports ID `0x314d474e` and version `0x00000100`.
It accepts MODE=0 only. Matching P4/T32 and P8/T32 board images have
[separate physical results](../results/ddr_gemm/p8_scaling/README.md); the full v1
overlap contract remains under development. `ENABLE_OVERLAP=1` selects the
[validated overlap path](ddr-overlap.md), version `0x00000200`, with MODE=0/1.
It replaces the serial job controller and DMA with the validated shell,
tagged scheduler and duplex contexts, and enables concurrent local ports.
Transport, registers, host arbitration and the shared AXI engine remain common.
Its qualification is recorded separately from the serial board checkpoints.

## Connections

```text
UART PHY bytes
      |
      v
COBS / CRC / retry transport
      |
      v
Packet command backend ----- register requests ----> Register bank
      |                                                  |
      | host MEM_READ / MEM_WRITE                        | START
      |                                                  v
      |                                            Job controller
      |                                                  |
      |                                          Tile DMA + compute
      |                                                  |
      +--------------------+-----------------------------+
                           |
                    Whole-operation owner
                           |
                     AXI burst engine
                           |
              SmartConnect -> MIG -> DDR2
```

The register, job, DMA and bank contracts remain in
[ddr-registers.md](ddr-registers.md), [ddr-job.md](ddr-job.md) and
[tile-dma.md](tile-dma.md). The command backend owns host memory movement;
the job controller owns matrix work. They share the existing burst buffers,
not the local operand/result ports.

## Packet commands

The [transport contract](specification.md#host-transport-and-software-contract)
defines COBS, CRC32, sequence replay and little-endian fields. Transport
validation precedes backend admission. The backend snapshots opcode, length,
payload and busy state. A later packet cannot alter an accepted command.

| Opcode | Payload | Successful data |
| --- | --- | --- |
| `0x01` PING | u32 nonce | nonce, ID, VERSION |
| `0x02` READ_REG | u16 register address | u32 value |
| `0x03` WRITE_REG | u16 address, u32 value | Empty; START waits for descriptor validation |
| `0x04` MEM_READ | u32 DDR address, u16 byte length | Exactly length bytes |
| `0x05` MEM_WRITE | u32 DDR address, u16 byte length, data | Empty; all writes acknowledged |

Memory lengths must be 8..240 bytes and multiples of eight. Addresses must be
eight-byte aligned. Widened `address + length` must remain within DDR_BYTES;
the default is 128 MiB. Decoded command payload/length errors return BAD_CMD;
alignment or window errors return BAD_ADDR. All checks precede memory requests.
Unknown opcodes return BAD_CMD. A register access receives the register bank's
status. Invalid COBS, CRC, version or frame lengths are discarded by transport
without a response or command execution.

The transport prepends the u16 response status and handles sequence replay.
The backend's data portion is at most 240 bytes, fitting the 256-byte packet
payload limit with status. An exact duplicate reuses the cached response
without another START or write; a same-sequence different command is rejected.

## Memory ownership and completion

Host memory access requires an idle, calibrated, nonfatal job engine. A busy
state captured at packet admission remains a rejection even if the old job
finishes during decoding. Host ownership starts before the first local burst
command and blocks START until the complete operation has finished.

```text
Read:  validate -> own -> issue AR -> receive/deliver -> terminal -> finish
Write: validate -> own -> collect -> independent AW/W -> B -> terminal -> finish

burst beats = min(words remaining, 16, words before next 4 KiB boundary)
```

Each burst finishes before the next is offered. Host writes enable all eight
byte lanes. A 240-byte packet can span several bursts, including a 4 KiB split.
Reads check the delivered tag, beat index, final flag and terminal count.
The first burst length is captured when a validated memory packet is accepted.
Each later length is captured with the next address and remaining-word count
after a successful nonfinal terminal. Commands use that stored length, so
backpressure and fault draining cannot alter their geometry. This moves planning
before command admission without adding a state or watchdog cycle.
Write data are retained separately from incoming command storage so diagnostic
packets cannot change a write still draining after a fault.

The owner multiplexes command/data/completion ports before the shared burst
engine. It never switches with outstanding AXI or local response work.
Read success requires all requested data and successful terminal completion;
write success requires every B response. Success also waits for AXI quiescence.
Matrix counters exclude host transfers because host ownership and active
matrix execution cannot overlap.

## Fault handling

Memory/protocol errors, loss of calibration or no-progress watchdog expiry
latch the first host fatal status. The configured WATCHDOG_LIMIT applies to
host movement too; actual command, data, terminal or AXI handshakes reset its
timer. Register polling does not.

The host owner snapshots the limit when accepting a memory operation. A limit
of N expires on the Nth consecutive busy edge without progress; progress on
that edge resets the timer instead. The stored comparison threshold is N-1
(zero for the defensive limit-0 case), calculated at admission. The counter
starts at zero and advances by one, so its first equality with that fixed
threshold is sufficient to latch the fault. It saturates rather than wrapping;
the fatal latch preserves the error independently of subsequent drain progress.

Fault detection stops new memory work and controls draining on the detecting
edge. The packet reply uses the registered first-error code one edge later,
keeping the watchdog/protocol chain out of the 1,920-bit response buffer.
Any word captured on that detecting edge remains internal and is cleared
before an error reply becomes valid. A fault on the final response-decision
edge suppresses success; calibration is required until that decision.
Held responses remain unchanged under transport backpressure.

```text
Before edge       Detecting edge          Following edge
MEMORY, no fault  latch first error       clear payload, enter REPLY
                  stop/drain memory       publish registered error
                  suppress success       keep draining obligations
```

A fatal response can reach software while an issued memory obligation remains
hung. Host ownership stays asserted until that obligation drains; PING and
register reads remain available. Further memory commands and START cannot
reuse the path. The job controller receives the registered fatal status and
publishes ERROR/RESET_REQUIRED without altering the previous job counters.

Already issued AXI VALID signals remain owned by the burst engine. A timeout
cannot withdraw them. Accepted local write collection continues with retained
data unless the burst engine explicitly cancels it with a terminal error.
An unaccepted local command may cancel on fatal. Reset must cover the entire
subsystem and vendor path together; warm-reset board qualification remains
separate from these protocol guarantees.

## Portable verification

`make test-ddr-core PYTHON=.venv/bin/python` runs host-library unit tests and
the complete packet/AXI RAM subsystem. Results, coverage and source identities
are generated under ignored `build/test_ddr_core/`.
The [C gather regression](../results/ddr_gemm/gather4/regression/README.md)
passes 24 subsystem tests across P4/T8, P4/T32, P8/T8 and P8/T32: 28 complete
jobs, 5,056 compared outputs and 3,652 command/response pairs. It includes
96 invalid memory commands, 16 malformed frames, eight duplicate replays and
eight sequence conflicts. The saved summaries identify the tested RTL and
verification sources.
Byte-level transport tests
do not establish UART pin timing, vendor DDR behavior, routed full-system
timing or board performance. Those require separate integration results.

## Cycle attribution

`make profile-ddr PYTHON=.venv/bin/python` runs the production portable RTL
with two explicit behavioral memory models, at P4 and a 100 MHz clock.
T8 and T32 use the same matrices, guarded DDR addresses and row strides.
The RAM is initialized directly before each job; configuration, START and
frozen counter reads still use framed packets. This separates the core
schedule from simulated host-loading time.

The profiler samples settled inputs before each rising edge. Registered
START acceptance identifies the beginning of the job. It partitions edges
`accepted + 1` through the final successful B handshake by the job controller's
state; the categories must sum exactly to `JOB_CYCLES`:

```text
A load -> BT load -> local compute -> C store -> next macrotile
                       |
                       +-- active microtile schedule
                       +-- launch, prefetch and completion overhead
```

Active schedule cycles exclude local prefetch and must equal
`COMPUTE_CYCLES`. The two input phases must equal `INPUT_WAIT_CYCLES`.
Read/write beats, enabled write bytes and stalled data-channel cycles are
checked independently against the frozen counters. Host memory ownership
must stay inactive throughout the job.

Each burst has a second, finer attribution:

```text
Read:  local command -> AR -> first R -> last R -> last bank load -> local done
Write: local command -> last collected C word -> max(AW, last W) -> B -> local done
```

Consecutive timestamp differences form disjoint durations. AW and W may
overlap, so their separate latencies are not added. Local read delivery and
bank writes also overlap through the held operand word. The final write's
`B -> local done` interval is outside `JOB_CYCLES`; completion publication
latency is recorded separately. VALID-without-READY counts are contained
diagnostics, not extra categories to add to the job time.

The unstalled AXI RAM model establishes the local schedule. The latency
model adds eight cycles before each burst's first read word and six cycles
before each final write word is committed by the RAM model, delaying B.
Delayed callbacks resume in the read-only phase after the coincident rising
edge, preserving the baseline model's response-queue timing. A timer that
returns before that edge would allow the idle AXI source to catch it and
shorten the intended interface delay by one cycle.
For this serial controller, the runner requires:

```text
added job cycles = 8 * read bursts + 6 * write bursts
```

Every output, complete input allocation, C padding and allocation guard is
checked in both models. These simulations quantify this RTL under stated
memory responses; they do not measure MIG latency or physical DDR bandwidth.

The current [P4/T32 board comparison](../results/ddr_gemm/gather4/board/README.md)
checks the revised result-gathering path independently. For dense 32x32x256,
the 30-run median is 34,082 job cycles, with 17,088 active schedule cycles in
every run. Useful utilization includes the entire DDR job:

```text
useful operations = 2 * 32 * 32 * 256 = 524,288
job time          = 34,082 / 100,000,000 = 340.82 us
useful throughput = 524,288 / 340.82 us = 1.538 GOPS
P4 utilization    = 32 * 32 * 256 / (4 * 4 * 34,082) = 48.1%
active fraction   = 17,088 / 34,082 = 50.1%
input/output bytes = 2,048 * 8 + 4,096 = 20,480
```

The remaining job cycles include input movement, result gathering/storing and
controller overhead. They are not a direct DDR latency measurement. Similarly,
dividing these bytes by job time gives average traffic over the complete job,
not the memory's achievable bandwidth. The portable trace supplies finer
attribution; the board supplies actual job counters. Reducing C gathering saves
512 cycles in the isolated model and a measured 555 median cycles across the
separate physical runs. That small overall gain leaves substantial work for
array scaling and transfer concurrency; it does not predict their speedup.

## Physical array scaling

The [P4/P8 comparison](../results/ddr_gemm/p8_scaling/README.md) uses the same
28 build sources, six host sources, signed matrix bytes, layouts and 100 MHz
clock. Both serial images pass 48 jobs with every output and allocation guard
checked. Dense results use 30 observations per image:

| 32x32x256 DDR-resident job | P4 | P8 |
| --- | ---: | ---: |
| Job cycles: min / median / max | 34,025 / 34,103 / 34,241 | 21,195 / 21,281.5 / 21,366 |
| Median job latency | 341.03 us | 212.815 us |
| Median useful throughput | 1.537 GOPS | 2.464 GOPS |
| Active microtile schedule cycles | 17,088 | 4,464 |
| Active schedule / median job | 50.1% | 21.0% |
| Median input-loading phases | 13,187.5 cycles | 13,172.5 cycles |
| Accepted read / write beats | 2,048 / 512 | 2,048 / 512 |
| Useful output bytes | 4,096 | 4,096 |

```text
P8 job time       = 21,281.5 / 100,000,000 = 212.815 us
P8 useful GOPS    = 524,288 / 212.815 us = 2.464 GOPS
P8 utilization    = 32 * 32 * 256 / (8 * 8 * 21,281.5) = 19.2%
Median job speedup = 34,103 / 21,281.5 = 1.60247x
```

P8 reduces the compute schedule by 3.83x, but leaves similar transfer/control
cost. INPUT_WAIT_CYCLES counts the serial controller's A/BT request and
completion phases; it includes planning, AXI response latency and delivery
of validated burst buffers into banks. Its 61.9% share of the P8 median job
identifies the input path as the next optimization target. READ_STALL_CYCLES
is zero because the burst engine keeps RREADY high; that counter cannot
establish whether DDR commands arrive efficiently or memory bandwidth is
saturated. The noncompute remainder also includes prefetch/launch and result
collection. This is a counter-based diagnosis, not a disjoint physical trace.

Tiny shapes do not benefit uniformly: the 1x1x1 median rises from 204 to 217
cycles because P8 has a longer scheduled fill/drain with unused lanes. Runs
were sequential by image, not interleaved, and retain DDR timing variation.
The four-read subsystem revision now has its own physical qualification.
A single 32x32 dense tile has no successor tile to overlap.

## Physical memory revision

Build `0xd558a543` passes [48 board jobs](../results/ddr_gemm/read4/host_geometry/board/README.md)
at P8/T32 and 100 MHz. The [revision comparison](../results/ddr_gemm/read4/board_comparison/README.md)
retains the matched workloads, distributions and source changes. Every output,
input allocation, C padding and guard is
checked; zero transport retries or rejected frames occur. The dense 30-run
measurement changes as follows:

| 32x32x256 DDR job | Saved one-read P8 | Revised four-read P8 |
| --- | ---: | ---: |
| Job cycles: min / median / max | 21,195 / 21,281.5 / 21,366 | 11,546 / 11,576.5 / 11,641 |
| Median useful GOPS | 2.464 | 4.529 |
| Active compute cycles | 4,464 | 4,464 |
| Median input-loading phases | 13,172.5 | 3,468 |
| Read / write beats | 2,048 / 512 | 2,048 / 512 |
| Useful output bytes | 4,096 | 4,096 |

```text
revised job time = 11,576.5 / 100,000,000 = 115.765 us
useful GOPS     = 524,288 / 115.765 us = 4.529
job speedup     = 21,281.5 / 11,576.5 = 1.83834x
```

Lower input-loading time explains the improvement while compute and useful
traffic stay fixed. INPUT_WAIT_CYCLES includes request planning, DDR response
latency and validated bank delivery; it does not isolate physical bandwidth.
These are sequential same-workload runs of two subsystem revisions. DMA,
burst and host-control sources changed, so attributing the entire difference
to the read-depth parameter would require a same-source one-read board build.
Inter-tile load/compute/store overlap is still absent.
