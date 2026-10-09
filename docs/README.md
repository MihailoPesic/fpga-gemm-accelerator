# Technical documentation

The current implementation is the Nexys A7-50T P8/T32 accelerator, image
`0x9d4beb4d`: signed INT8 inputs, INT32 results, four outstanding reads,
100 MHz core and 1 Mbaud UART. Serial and overlapping schedules run on the
same image. It is a measured development release; [status](status.md) records
the cold-start restriction and remaining full-v1 requirements.

## Review the project

| Read | What it answers |
| --- | --- |
| [Architecture](architecture.md) | How operands, results and control move through the current system; buffer ownership, clocks and completion. |
| [Measurements](measurements.md) | What changed between T8 serial, T32 serial and T32 overlap; measured throughput, traffic and limitations. |
| [Verification](verification.md) | Which arithmetic, interface and system checks support the claims, and their scope. |
| [Current implementation reports](../results/ddr_overlap/release_1mbaud/t32/README.md) | Routed timing, resources, clocks, CDC and the worst control path; this static archive has its original pre-board scope. |
| [Current board evidence index](../results/ddr_overlap/release_1mbaud/README.md) | The later physical measurements, maximum-size checks, sustained run and CLI/API demo for the same image. |
| [Host API](host-gemm.md), [build and test](testing.md), [board workflow](ddr-board.md) | How to use supplied matrices, reproduce checks and build/program the FPGA. |

## Module contracts

These documents describe the portable RTL and its integration boundaries.
Read a module contract together with its linked source and test.

| Area | Documents |
| --- | --- |
| Arithmetic and fixed schedule | [Compute](compute.md): PE pipeline, skew, feed/drain edges and tail masks. |
| Local storage | [Operand memory](memory.md): bank addressing and BRAM prefetch. [Local matrix engine](tile-engine.md): microtile iteration, result banks and concurrent ports. |
| Memory movement | [AXI burst primitive](axi-burst.md): independent channels, buffers and responses. [Row sequencer](dma-rows.md): addresses, page splits and strobes. [Tile DMA](tile-dma.md): bank delivery and independent load/store contexts. |
| Scheduling and jobs | [Tagged scheduler](tile-scheduler.md): separate input/result lifetimes and ordered retirement. [Selectable DDR overlap](ddr-overlap.md): validated MODE0/1 jobs, fatal drain and public counters. |
| Control and transport | [DDR registers](ddr-registers.md): local bus, descriptor map and frozen snapshots. [DDR subsystem](ddr-core.md): packet commands, host/DMA exclusion and configuration selection. [Host API](host-gemm.md): packing, allocation and job lifecycle. |
| Board platform | [AXI DDR platform](axi-platform.md): MIG configuration and SmartConnect conversion. [Memory compatibility](ddr-memory-compatibility.md): preset versus physical device identification and timing limits. [Board integration](ddr-board.md): wrapper, constraints and cold-start procedure. |

## Requirements and development history

The [original v1 specification](specification.md) states the target contract;
it is not the implemented interface or a claim that every release gate is
complete. Use the current module contracts and [status](status.md) for the
development VERSION `0x200` implementation.

[Design decisions](decisions.md) records interface and timing tradeoffs.
[Development history](history.md) links the native DDR baseline, BRAM preview,
diagnostic, serial designs and failed routes. Their records retain their own
source/image identities; they do not qualify a later build.
