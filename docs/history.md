# Development history

The current architecture is documented in [architecture.md](architecture.md).
This index preserves the earlier checkpoints and the reasons for subsequent
changes. Each linked record belongs to its saved source/configuration and,
where applicable, bitstream. Earlier passes do not qualify a later image.
Original failures remain failures; later corrections have separate evidence.

## Checkpoints

| Checkpoint | Purpose and retained evidence |
| --- | --- |
| [Native DDR baseline](../results/README.md#archived-intermediate-runs) | Historical UART bridge using MIG's native application port. It is not the current byte-addressed AXI/GEMM interface; native `app_addr` values cannot be copied into AXI descriptors. |
| [Compute core](../results/verification/README.md) | Signed PE, P4/P8 skewed array, exact drain schedule, deliberate defect detection and standalone timing feasibility. No banks, DMA or complete board job. |
| [Operand memory](../results/README.md#archived-intermediate-runs), [local engine](../results/README.md#archived-intermediate-runs) | Synchronous banked BRAM, prefetch, microtile iteration and result readback. These local boundaries exclude DDR transfers. |
| [BRAM preview](../results/README.md#archived-intermediate-runs) | UART-controlled P4/T32 matrix computation, with 180 board jobs and 90,510 compared outputs. Smaller limits and no DDR readiness; [interface](preview.md). |
| [AXI DDR diagnostic](../results/README.md#archived-intermediate-runs) | Pattern/masked-write checks through the custom burst engine, SmartConnect and MIG. Selected memory locations, not full-memory qualification, bandwidth or GEMM; [contract](ddr-diagnostic.md). |
| [Row planner](../results/README.md#archived-intermediate-runs), [serial tile DMA](../results/README.md#archived-intermediate-runs) | Row/page splitting and useful-byte masks, followed by bank load/compute/store integration against behavioral AXI RAM. |
| [Serial job controller](../results/README.md#archived-intermediate-runs), [registers](../results/README.md#archived-intermediate-runs), [packet subsystem](../results/README.md#archived-intermediate-runs) | Validated descriptors, external macrotile iteration, status/counters and host/DMA exclusion; [serial job contract](ddr-job.md). |
| [Serial P4/P8 board integration](../results/README.md#archived-intermediate-runs) | Vendor, routed and physical evidence for the earlier serial hierarchy. [Gathering](../results/README.md#archived-intermediate-runs) and [P4/P8 scaling](../results/README.md#archived-intermediate-runs) have matched workload comparisons. |
| [Four-read serial DMA](../results/README.md#archived-intermediate-runs) | Image `0xd558a543`, P8/T32 at 100 MHz. Matched one-read/four-read workloads separate read concurrency from array scaling. |
| [Concurrent local ports](../results/verification/README.md#injected-rtl-defect-detection), [duplex DMA](../results/README.md#archived-intermediate-runs), [tagged scheduler](../results/README.md#archived-intermediate-runs) | Separately verified prerequisites for independent load/compute/store ownership. Their portable results do not themselves qualify a physical overlap image. |
| [Selectable overlap at 115200 baud](../results/README.md#archived-intermediate-runs) | Image `0x2c680af7`, VERSION `0x200`, P8/T32/READ4. Both scheduling modes, matched board measurements, maximum-size and sustained-workload evidence retain that image's identity. |
| [Current 1 Mbaud release](../results/README.md) | T32 image `0x9d4beb4d`, matched T8 baseline and controlled reuse/overlap comparisons. [Measurements](measurements.md) summarizes the current result. |

These are configurations of shared source in one repository. Generated
Vivado projects select different tops/parameters; integration connects RTL
interfaces rather than merging generated projects or bitstreams. The old
root `accelerator nexys.xpr` belongs to the native DDR baseline.

## Failures and design changes

| Record | What changed |
| --- | --- |
| [Initial compute timing](../results/README.md#archived-intermediate-runs) | Registered drain selection removed a combinational schedule path. The final timing harness also changed, so its slack difference is not a controlled performance measurement. |
| [Vendor reference failure](../results/README.md#archived-intermediate-runs) | A DUT-free reproducer isolated unknown expected values in an XSim expression. Explicit arithmetic intermediates passed the reduced test; the original integration failure was retained and complete integration required a new pass. |
| [C gathering comparison](../results/README.md#archived-intermediate-runs) | Removing a normal-path preparation cycle reduced collection from five to four cycles per 64-bit result word while preserving fault-fill and response obligations. |
| [Initial four-read route](../results/README.md#archived-intermediate-runs), [registered reply](../results/README.md#archived-intermediate-runs), [host geometry](../results/README.md#archived-intermediate-runs) | Registered error replies and captured burst geometry shortened control paths; each revision kept its own functional and physical checks. |
| [Initial overlap route](../results/implementation/README.md#key-engineering-observations), [post-route optimization](../results/implementation/README.md#key-engineering-observations) | Scalar fault detection separated admission/control from error-code payload selection. Required physical optimization became part of the reproducible flow. |
| [Stopped sustained run](../results/board/README.md#30-minute-endurance-run) | A host timeout around Windows Modern Standby remained a failed run. Read-only recovery checked retained data separately; the replacement sustained run started from zero elapsed time. |

[Design decisions](decisions.md) explains these changes and their tradeoffs.
[Verification](verification.md) distinguishes production passes, deliberate
defect witnesses, behavioral/vendor simulation, physical results and scoped
formal checks. [Status](status.md) records unresolved reset/platform gates.
