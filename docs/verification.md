# Verification overview

The current physical checkpoint is P8/T32, READ4, 100 MHz, selectable MODE0/1,
development VERSION 0x200, 1 Mbaud image `0x9d4beb4d`. Its
[dense comparison](../results/ddr_overlap/release_1mbaud/board/t32/dense/README.md)
passes 60 jobs and 3,932,160 output comparisons. Earlier image `0x2c680af7`
has separate shape and repeatability records below. Each result retains its own
source hashes and, where applicable, bitstream identity. Earlier P4 and serial
results establish their recorded configurations; they do not qualify a later
image. [Testing](testing.md) gives reproduction commands.

| Requirement group | Completed checks | Evidence |
| --- | --- | --- |
| Signed arithmetic and pipeline | All 65,536 INT8 pairs; reduction extremes, zeros, alternating signs, bubbles and clear | [Compute core](../results/core/README.md) |
| Array skew, cycle schedule and PE tails | P4/P8; K boundary cases; 500 seeded microtiles per P; reduction tags and exact final drain edge | [Compute contract](compute.md), [core evidence](../results/core/README.md) |
| Bank mapping, BRAM latency and masking | Both buffers, bank groups, word boundaries, row/column tails, complete local C readback and nonzero padding | [Operand banks](../results/operand_memory/README.md), [local engine](../results/tile_engine/README.md) |
| AXI and DMA obligations | Independent channel stalls, delayed B, row/4 KiB splits, ring wrap, read occupancy 0..4, malformed/error responses and accepted-work draining | [AXI tests](../results/axi/README.md), [read queue](../results/axi/read_queue/README.md), [tile DMA](../results/tile_dma_duplex/README.md) |
| Descriptor and job control | Widened range validation, disjoint allocations, invalid START without DMA, busy exclusions, first faults, calibration loss, watchdog and final-B completion | [Corrected integrated regression](../results/ddr_overlap/timing_predicate/README.md) |
| Overlap ownership | Independent input/result IDs, retained C sets during blocked stores, reserve-before-launch, both modes and three-way stage activity | [Scheduler](../results/tile_scheduler/README.md), [local concurrent ports](../results/tile_overlap_ports/README.md) |
| UART and register semantics | COBS/CRC, malformed/truncated/oversized frames, duplicate START replay, sequence conflicts, held replies, status and frozen counters | [Transport](preview.md), [packet/core evidence](../results/ddr_overlap/timing_predicate/README.md) |
| Deliberate defects | Unsigned arithmetic, stale product and early drain; three local ownership defects; two external-read stop defects | [Core mutations](../results/core/README.md), [ownership mutations](../results/tile_overlap_ports/README.md), [read-stop mutations](../results/ddr_gemm/read4/mutations/README.md) |
| Scoped formal safety | Fixed row-planner bounded checks; one-beat FIFO base/induction; reduced scheduler MODE0/1 bounded checks, with reachable witnesses | [Row planner](../results/dma_rows/formal/README.md), [FIFO/scheduler](../results/buffer_formal/README.md) |
| 1 Mbaud vendor and physical integration | Three framed-UART jobs through SmartConnect/MIG/DDR model; routed setup/hold, clocks, CDC, reset selectors and bus-skew review | [Own T32 implementation](../results/ddr_overlap/release_1mbaud/t32/README.md) |
| 1 Mbaud dense correctness | 30 matched serial/overlap pairs; 3,932,160 output comparisons and full guarded allocations | [Dense board record](../results/ddr_overlap/release_1mbaud/board/t32/dense/README.md) |
| 1 Mbaud maximum-size correctness | One 1024x1024x256 job per mode; 2,097,152 outputs and complete allocation snapshots; independent INT64 recomputation | [Maximum-size record](../results/ddr_overlap/release_1mbaud/board/t32/maximum/README.md) |
| 1 Mbaud T32 shape grid | 960 jobs across 16 shapes, 30 samples per mode; 15,981,960 outputs and complete allocations checked | [Full benchmark grid](../results/ddr_overlap/release_1mbaud/board/t32/benchmark/README.md) |
| 1 Mbaud T32 repeatability | Fresh 30.06-minute host-paced run; 592 mixed jobs, 550,634 outputs, both modes and zero retries | [Sustained run](../results/ddr_overlap/release_1mbaud/board/t32/endurance/README.md) |
| 1 Mbaud T8 serial baseline | 480 jobs, 16 matched shapes and 30 samples per case; 7,990,980 outputs and guarded allocations | [T8 board grid](../results/ddr_overlap/release_1mbaud/board/t8/benchmark/README.md) |
| Controlled tiling/overlap | 1,440 jobs, 48 series; matching input/layout/sentinel signatures, independent counter and ratio checks, three plots | [Controlled comparison](../results/ddr_overlap/release_1mbaud/comparison/README.md) |
| 115200-baud checkpoint correctness | 48 jobs per mode, 30 matched benchmark pairs and a complete 1024x1024x256 MODE1 comparison; input/padding/guard checks | [Board plan and comparison](../results/ddr_overlap/timing_predicate/final_build/board/README.md), [maximum shape](../results/ddr_overlap/timing_predicate/final_build/maximum/README.md) |
| 115200-baud checkpoint repeatability | 368 mixed jobs, 184 per mode, 342,286 outputs and 5,319,624 input/padding/guard bytes checked over 30.52 continuous host-paced minutes | [Sustained run](../results/ddr_overlap/timing_predicate/final_build/endurance/README.md) |

Coverage is recorded by layer. The array/bank records cover geometry and K/tail
classes; DMA records cover burst lengths, read occupancy and independent stalls;
scheduler/job records cover modes, ownership transitions and faults. The
corrected integrated checkpoint passes 154 tests and 1,056 complete jobs,
including 800 seeded shell jobs. Directed cases supplement random seeds.
Seed counts and covered bins are not exhaustive proof of the job space.

The new T32 grid and sustained run compare every output and complete allocation
during board execution. Their retained records support independent metadata,
traffic, schedule and provenance checks; no raw per-job output or UART replay
is claimed. The maximum-size record retains the bytes needed for numerical replay.

The portable checks use behavioral AXI memory and an independent wide-integer
oracle. Their fault injection does not model a physically failing DDR device.
The vendor fixture uses FAST calibration, ideal PCB delays and accelerated
UART timing; its retained startup warning and electrical limits are described
in the vendor record. Formal claims are limited to the named models,
assumptions and methods. None proves the full accelerator or DDR PHY.

Current board operation requires cold power-up. Warm-reset DDR clock/CKE timing
remains unresolved. The complete physical FPGA/DDR suffixes and PCB revision
also remain unrecorded. Passing routed timing applies to the exact checkpoint
and declared constraints; it does not establish a higher clock or electrical
margin across boards and temperatures.
The [clock-review addendum](../results/ddr_overlap/timing_predicate/final_build/clock_review_addendum.md)
corrects one historical derived table count without changing the sealed reports.

The T8 baseline and controlled T8-serial/T32-serial/T32-overlap grid now pass
with exact source and image identities. The original
specification also asks for a separately measured resident-array run and
read-only/write-only/mixed DDR bandwidth. COMPUTE_CYCLES within a DDR job
does not establish the former, and accepted traffic counters alone do not
establish the latter. [Status](status.md) tracks completed gates.
