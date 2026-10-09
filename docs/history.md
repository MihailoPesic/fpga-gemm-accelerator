# Development history

The current architecture is documented in [architecture.md](architecture.md).
This index preserves the earlier checkpoints and the reasons for subsequent
changes. Each linked record belongs to its saved source/configuration and,
where applicable, bitstream. Earlier passes do not qualify a later image.
Original failures remain failures; later corrections have separate evidence.

## Checkpoints

| Checkpoint | Purpose and retained evidence |
| --- | --- |
| [Native DDR baseline](../results/core/native_baseline/) | Historical UART bridge using MIG's native application port. It is not the current byte-addressed AXI/GEMM interface; native `app_addr` values cannot be copied into AXI descriptors. |
| [Compute core](../results/core/README.md) | Signed PE, P4/P8 skewed array, exact drain schedule, deliberate defect detection and standalone timing feasibility. No banks, DMA or complete board job. |
| [Operand memory](../results/operand_memory/README.md), [local engine](../results/tile_engine/README.md) | Synchronous banked BRAM, prefetch, microtile iteration and result readback. These local boundaries exclude DDR transfers. |
| [BRAM preview](../results/preview/README.md) | UART-controlled P4/T32 matrix computation, with 180 board jobs and 90,510 compared outputs. Smaller limits and no DDR readiness; [interface](preview.md). |
| [AXI DDR diagnostic](../results/ddr_platform/README.md) | Pattern/masked-write checks through the custom burst engine, SmartConnect and MIG. Selected memory locations, not full-memory qualification, bandwidth or GEMM; [contract](ddr-diagnostic.md). |
| [Row planner](../results/dma_rows/README.md), [serial tile DMA](../results/tile_dma/README.md) | Row/page splitting and useful-byte masks, followed by bank load/compute/store integration against behavioral AXI RAM. |
| [Serial job controller](../results/ddr_job/README.md), [registers](../results/ddr_registers/README.md), [packet subsystem](../results/ddr_core/README.md) | Validated descriptors, external macrotile iteration, status/counters and host/DMA exclusion; [serial job contract](ddr-job.md). |
| [Serial P4/P8 board integration](../results/ddr_gemm/README.md) | Vendor, routed and physical evidence for the earlier serial hierarchy. [Gathering](../results/ddr_gemm/gather4/board/README.md) and [P4/P8 scaling](../results/ddr_gemm/p8_scaling/README.md) have matched workload comparisons. |
| [Four-read serial DMA](../results/ddr_gemm/read4/board_comparison/README.md) | Image `0xd558a543`, P8/T32 at 100 MHz. Matched one-read/four-read workloads separate read concurrency from array scaling. |
| [Concurrent local ports](../results/tile_overlap_ports/README.md), [duplex DMA](../results/tile_dma_duplex/README.md), [tagged scheduler](../results/tile_scheduler/README.md) | Separately verified prerequisites for independent load/compute/store ownership. Their portable results do not themselves qualify a physical overlap image. |
| [Selectable overlap at 115200 baud](../results/ddr_overlap/timing_predicate/final_build/board/README.md) | Image `0x2c680af7`, VERSION `0x200`, P8/T32/READ4. Both scheduling modes, matched board measurements, maximum-size and sustained-workload evidence retain that image's identity. |
| [Current 1 Mbaud release](../results/ddr_overlap/release_1mbaud/README.md) | T32 image `0x9d4beb4d`, matched T8 baseline and controlled reuse/overlap comparisons. [Measurements](measurements.md) summarizes the current result. |

These are configurations of shared source in one repository. Generated
Vivado projects select different tops/parameters; integration connects RTL
interfaces rather than merging generated projects or bitstreams. The old
root `accelerator nexys.xpr` belongs to the native DDR baseline.

## Failures and design changes

| Record | What changed |
| --- | --- |
| [Initial compute timing](../results/core/initial_timing/) | Registered drain selection removed a combinational schedule path. The final timing harness also changed, so its slack difference is not a controlled performance measurement. |
| [Vendor reference failure](../results/ddr_gemm/vendor_oracle_failure/README.md) | A DUT-free reproducer isolated unknown expected values in an XSim expression. Explicit arithmetic intermediates passed the reduced test; the original integration failure was retained and complete integration required a new pass. |
| [C gathering comparison](../results/ddr_gemm/gather4/profile/README.md) | Removing a normal-path preparation cycle reduced collection from five to four cycles per 64-bit result word while preserving fault-fill and response obligations. |
| [Initial four-read route](../results/ddr_gemm/read4/initial_timing/README.md), [registered reply](../results/ddr_gemm/read4/registered_reply/initial_timing/README.md), [host geometry](../results/ddr_gemm/read4/host_geometry/route_probe/README.md) | Registered error replies and captured burst geometry shortened control paths; each revision kept its own functional and physical checks. |
| [Initial overlap route](../results/ddr_overlap/initial_timing/README.md), [post-route optimization](../results/ddr_overlap/timing_predicate/postroute_physopt/README.md) | Scalar fault detection separated admission/control from error-code payload selection. Required physical optimization became part of the reproducible flow. |
| [Stopped sustained run](../results/ddr_overlap/release_1mbaud/board/t32/endurance_failed/README.md) | A host timeout around Windows Modern Standby remained a failed run. Read-only recovery checked retained data separately; the replacement sustained run started from zero elapsed time. |

[Design decisions](decisions.md) explains these changes and their tradeoffs.
[Verification](verification.md) distinguishes production passes, deliberate
defect witnesses, behavioral/vendor simulation, physical results and scoped
formal checks. [Status](status.md) records unresolved reset/platform gates.
