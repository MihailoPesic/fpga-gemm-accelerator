# Technical documentation

The implemented builds and remaining integration work are tracked in
[status.md](status.md). The current DDR build performs board-validated GEMM in both modes;
the BRAM preview and AXI DDR diagnostic retain their earlier evidence.
P4/P8 serial board images and four-read P8 DMA retain their evidence. Inter-tile
overlap passes portable tests, vendor simulation, the
[1 Mbaud implementation gates](../results/ddr_overlap/release_1mbaud/t32/README.md)
and [60 dense board jobs](../results/ddr_overlap/release_1mbaud/board/t32/dense/README.md).
Thirty matched pairs measure 11.298 useful GOPS at 256x256x256, with a 2.495x
median paired speedup over serial mode on the same image.
The same image's [maximum-size checks](../results/ddr_overlap/release_1mbaud/board/t32/maximum/README.md)
compare and independently replay 2,097,152 outputs across both modes.
Its [complete shape grid](../results/ddr_overlap/release_1mbaud/board/t32/benchmark/README.md)
passes 960 jobs and 15,981,960 output comparisons across 16 shapes, with 30
samples per mode. Its [fresh sustained run](../results/ddr_overlap/release_1mbaud/board/t32/endurance/README.md)
passes 592 mixed jobs and 550,634 outputs over 30.06 host-paced minutes.
The [115200-baud maximum-shape check](../results/ddr_overlap/timing_predicate/final_build/maximum/README.md)
also compares all 1,048,576 outputs on that image.
Its [sustained run](../results/ddr_overlap/timing_predicate/final_build/endurance/README.md)
passes 368 mixed jobs in both modes over 30.52 continuous host-paced minutes.

## Architecture and requirements

| Document | Scope |
| --- | --- |
| [Architecture](architecture.md) | Module connections, data flow, clocks/resets, buffer ownership and completion boundaries |
| [Project status](status.md) | Implemented features, validation evidence and remaining work |
| [Design decisions](decisions.md) | Interface choices, timing changes and implementation tradeoffs |
| [Full-release specification](specification.md) | Target arithmetic, descriptors, memory/transport contracts and release acceptance criteria |

## Compute and local memory

| Document | Scope |
| --- | --- |
| [Compute](compute.md) | Signed PE pipeline, systolic skew and exact feed/drain schedule |
| [Operand memory](memory.md) | Bank addressing, synchronous BRAM prefetch and input-buffer exclusion |
| [Local matrix engine](tile-engine.md) | Microtile iteration, result banking, tails and local-job counters |
| [Tagged macrotile scheduler](tile-scheduler.md) | Separate input/result lifetimes, ordered load/compute/store cursors, serial/overlap modes and fatal drain; internal development boundary |

## Host interfaces and external memory

| Document | Scope |
| --- | --- |
| [BRAM preview](preview.md) | UART framing/retries, fixed register/memory map and board workflow |
| [AXI burst primitive](axi-burst.md) | Buffered read/write transactions, independent handshakes, completion and faults |
| [Row burst sequencer](dma-rows.md) | Validated row addresses, burst splitting and byte masks; command planning only |
| [Tile DMA adapters](tile-dma.md) | Operand-bank delivery, synchronous C reads, serial completion and independent load/store contexts |
| [Serial DDR job controller](ddr-job.md) | Descriptor validation, macrotile order, job completion, counters and fatal drain |
| [DDR register interface](ddr-registers.md) | Local bus, descriptor registers, START acknowledgement and frozen status/counters |
| [DDR subsystem](ddr-core.md) | Packet commands, shared host/DMA ownership, serial/selectable build paths and fault draining |
| [Selectable DDR overlap](ddr-overlap.md) | Validated MODE=0/1 jobs, independent buffer lifetimes, measured counter boundaries and registered fault draining |
| [DDR GEMM host API](host-gemm.md) | BT packing, DDR layouts, job lifecycle, validation and measurement scopes |
| [DDR GEMM board integration](ddr-board.md) | UART/platform wrapper, vendor build gates, cold-start programming and board workflow |
| [AXI DDR platform](axi-platform.md) | MIG configuration, SmartConnect width/clock conversion and vendor-model checks |
| [DDR2 memory compatibility](ddr-memory-compatibility.md) | Configured preset, physical chip identification, effective timings and remaining electrical checks |
| [DDR diagnostic](ddr-diagnostic.md) | Pattern/compare controller, registers, counters, board procedure and reset limits |

## Verification and results

[Build and test](testing.md) documents the portable, Vivado and board commands.
Source and test links are included in the mechanism documents.
The [verification overview](verification.md) maps requirement groups to their
completed evidence and remaining acceptance limits.

Saved results cover the [compute core](../results/core/README.md),
[operand memory](../results/operand_memory/README.md),
[local engine](../results/tile_engine/README.md),
[BRAM preview](../results/preview/README.md),
[AXI primitive](../results/axi/README.md),
[row sequencer](../results/dma_rows/README.md),
[tile DMA integration](../results/tile_dma/README.md),
[serial DDR jobs](../results/ddr_job/README.md),
[DDR registers](../results/ddr_registers/README.md),
[serial DDR packet subsystem](../results/ddr_core/README.md),
[DDR platform](../results/ddr_platform/README.md) and
[DDR GEMM build gates](../results/ddr_gemm/README.md).
The [serial cycle profile](../results/ddr_gemm/profile/README.md) separates
modeled transfer/control costs and compares T8/T32 reuse at fixed P4/clock.
The [result-gathering comparison](../results/ddr_gemm/gather4/profile/README.md)
retains the original source delta and matched before/after profiles.
The [P4/P8 board comparison](../results/ddr_gemm/p8_scaling/README.md) keeps
clock, source code, input bytes and traffic fixed while changing array size.
The [P8 memory revision comparison](../results/ddr_gemm/read4/board_comparison/README.md)
records the one-read and four-read images on the same workloads: dense median
latency falls from 21,281.5 to 11,576.5 cycles, with unchanged compute and traffic.
The [concurrent local-port prerequisite](../results/tile_overlap_ports/README.md)
has portable concurrency, serial compatibility and defect-detection evidence;
the selectable DDR build enables those ports under tagged ownership.
The [independent DMA prerequisite](../results/tile_dma_duplex/README.md)
adds separate load/store descriptors and completions, with a tested three-tile
pipeline and shared fatal draining. Its 40 portable tests pass across both
read depths and all four geometries.
The [tagged scheduler checkpoint](../results/tile_scheduler/README.md) passes
24 tests, 112 complete jobs and 71,632 output comparisons. It connects those
contexts under independent tile ownership for both modes. The integrated
[overlap job shell](ddr-overlap.md) now adds descriptor validation, production
START/status, watchdog and frozen counters. Its own portable and physical
gates identify the current source separately from these earlier checkpoints.
The [integrated portable regression](../results/ddr_overlap/README.md) passes
154 tests, 1,056 complete jobs and 322,799 output comparisons.
Each result identifies its tested configuration and scope.

The [bounded row-planner formal record](../results/dma_rows/formal/README.md)
preserves 18 assertions at READ1/4 through 20 timeframes for one fixed descriptor,
seven decoded reachability witnesses, assumptions, source snapshots and actual
tool transcripts. It does not prove the FIFO, scheduler or full accelerator.
The separate [FIFO and reduced scheduler proofs](../results/buffer_formal/README.md)
add a scoped inductive queue result and 32-timeframe ownership checks.
