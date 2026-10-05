# Project status

Updated 2026-10-05. Target: [specification](specification.md), with
[architecture](architecture.md) and [design decisions](decisions.md).

The P8/T32 DDR-backed accelerator works on the Nexys A7-50T at 100 MHz.
Its 1 Mbaud image is `0x9d4beb4d`, with four outstanding reads and selectable
serial/overlap scheduling. The [dense board record](../results/ddr_overlap/release_1mbaud/board/t32/dense/README.md)
passes 60 jobs and 3,932,160 output comparisons. At 256x256x256, 30 matched pairs
measure median 297,001 cycles / 11.298 useful GOPS in overlap mode and 741,063.5
cycles / 4.528 GOPS in serial mode; median paired speedup is 2.495x. Every
DDR tile load, result write and final B response is inside the job interval.
The host loads signed INT8 matrices, submits a job, reads every INT32 result
and checks memory guards; UART movement and validation are timed separately.

The same image also passes [maximum-size jobs in both modes](../results/ddr_overlap/release_1mbaud/board/t32/maximum/README.md):
M=N=1024,K=256, with 2,097,152 outputs checked. Complete retained DDR snapshots
permit independent INT64 numerical replay. Overlap takes 4,664,784 cycles /
46.64784 ms / 11.509 GOPS. This is one job per mode, not a sample distribution.

The first new-image [endurance attempt](../results/ddr_overlap/release_1mbaud/board/t32/endurance_failed/README.md)
failed after 381 validated jobs when READ_REG timed out around a recorded
Windows Modern Standby interval. A read-only reconnect independently checked
the pending job's 64 retained outputs and guards; this does not turn the failed
run into a pass. Its [replacement](../results/ddr_overlap/release_1mbaud/board/t32/endurance/README.md)
starts from zero elapsed endurance time and passes 592 mixed jobs, 550,634
outputs and full allocations over 30.06 continuous host-paced minutes, with
zero transport retries. The [complete T32 benchmark grid](../results/ddr_overlap/release_1mbaud/board/t32/benchmark/README.md)
also passes: 16 shapes, 30 samples per mode, 960 jobs and 15,981,960 output
comparisons. The recovered parent and child both exit zero; the reused dense
and maximum-size units retain their original times and seals.

The matched [T8 serial baseline](../results/ddr_overlap/release_1mbaud/board/t8/benchmark/README.md)
passes 480 jobs and 7,990,980 output comparisons after a separate cold start.
The [controlled comparison](../results/ddr_overlap/release_1mbaud/comparison/README.md)
contains 16 shapes, 48 series and 30 samples per series. At 256x256x256,
T8 serial takes median 1,698,969.5 cycles / 1.975 GOPS. T32 serial is 2.293x
faster and T32 overlap adds 2.495x, giving a 5.720x ratio of cycle medians.
Accepted input bytes fall 4x; input plus useful C bytes fall 3.4x. Larger tiles
also change burst and tile-control overhead, so runtime gains are not attributed
to operand reuse alone. Counters, plots and exact source/image identities are retained.

The 115200-baud selectable image `0x2c680af7` retains its separate 156-job,
maximum-shape and sustained-workload records below. Those checks do not qualify
the new image. The preceding serial image `0xd558a543` also retains its own evidence.
The first four-read DMA revision passed portable and vendor verification,
but its [first route](../results/ddr_gemm/read4/initial_timing/README.md)
missed setup timing by 0.470 ns. The registered host-error response revision
passed 72 portable packet/core tests and reduced failing endpoints to 14,
but still [missed setup by 0.342 ns](../results/ddr_gemm/read4/registered_reply/initial_timing/README.md).
Host burst geometry is now captured before command admission. This source
revision passes 80 portable packet/core tests and a
[100 MHz timing experiment](../results/ddr_gemm/read4/host_geometry/route_probe/README.md)
with +0.203/+0.014 ns setup/hold margins. Its
[vendor simulation](../results/ddr_gemm/read4/host_geometry/vendor/README.md)
passes both framed-UART jobs under `0xd558a543`. The
[production bitstream and reset review](../results/ddr_gemm/read4/host_geometry/routed/README.md)
also pass, with the same +0.203/+0.014 ns margins. After a fresh cold start,
the [48-job board suite](../results/ddr_gemm/read4/host_geometry/board/README.md)
passes all 49,593 outputs and 689,628 input/padding/guard bytes with zero
transport retries. Dense median latency is 11,576.5 cycles (115.765 us), or
4.529 useful GOPS. The saved one-read P8 median is 21,281.5 cycles;
the new subsystem is 1.83834x faster on the same workload. The
[revision comparison](../results/ddr_gemm/read4/board_comparison/README.md)
preserves all seven distributions, matched workloads and source changes.

## Working hardware checkpoints

| Build | Completed evidence |
| --- | --- |
| [BRAM preview](../results/preview/README.md) | P4/T32: 180 jobs, 90,510 compared outputs, routed 100 MHz timing |
| [DDR diagnostic](../results/ddr_platform/README.md) | Three cold-start memory-path checks; selected addresses, masks and guard bytes |
| [P4 serial DDR GEMM](../results/ddr_gemm/gather4/board/README.md) | 48 jobs; 49,593 compared outputs; dense median 34,103 cycles |
| [P8 serial DDR GEMM](../results/ddr_gemm/p8_serial/board/README.md) | 48 jobs; 49,593 compared outputs; dense median 21,281.5 cycles, 2.464 useful GOPS |
| [P8 four-read DDR GEMM](../results/ddr_gemm/read4/host_geometry/board/README.md) | 48 jobs; 49,593 compared outputs; dense median 11,576.5 cycles, 4.529 useful GOPS |
| [Selectable P8 DDR GEMM](../results/ddr_overlap/timing_predicate/final_build/board/README.md) | 48 jobs per mode plus 30 matched pairs; 344,946 compared outputs; 64x64x256 overlap median 25,130 cycles, 8.345 useful GOPS |
| [1 Mbaud P8 dense comparison](../results/ddr_overlap/release_1mbaud/board/t32/dense/README.md) | 30 matched pairs at 256x256x256; 3,932,160 outputs; overlap median 297,001 cycles / 11.298 useful GOPS; 2.495x paired speedup |
| [1 Mbaud P8 maximum-size check](../results/ddr_overlap/release_1mbaud/board/t32/maximum/README.md) | 1024x1024x256 in both modes; 2,097,152 outputs; one sample per mode and complete independent byte replay |
| [1 Mbaud T32 shape grid](../results/ddr_overlap/release_1mbaud/board/t32/benchmark/README.md) | 960 jobs, 16 shapes, 30 samples per mode; 15,981,960 outputs and complete guarded allocations checked |
| [1 Mbaud T32 repeatability](../results/ddr_overlap/release_1mbaud/board/t32/endurance/README.md) | 592 mixed jobs and 550,634 outputs over 30.06 continuous host-paced minutes; both modes and zero retries |
| [Matched P4/P8 comparison](../results/ddr_gemm/p8_scaling/README.md) | 1.60247x dense DDR-job speedup; unchanged matrix bytes, layouts, host and RTL sources, with P changed |
| [115200-baud P8 maximum-shape check](../results/ddr_overlap/timing_predicate/final_build/maximum/README.md) | Image 0x2c680af7, MODE1: 1024x1024x256; all 1,048,576 outputs checked; one job at 11.512 useful GOPS |
| [Repeatability](../results/ddr_gemm/gather4/repeatability/README.md) | Optimized P4 image: 432 jobs over 33.32 minutes, host-paced; no mismatches |

Earlier serial dense measurements use M=N=32,K=256 and include every tile load, result write
and final successful B response. Packing, UART movement and validation are
recorded separately. The 115200-baud P8 maximum-size test has its own identity
and complete output comparison. The earlier extended-repeatability result
belongs to its specified P4 image and does not qualify this P8 build.

The preceding serial P8 four-read route uses 72 DSPs and 20 RAMB36 equivalents, with
setup/hold margins +0.203/+0.014 ns at 100 MHz. These are full-system routed results,
separate from earlier core and BRAM timing harnesses.

## Serial DMA checkpoint

[Read-DMA results](../results/ddr_gemm/read4/README.md) bind the pre-timing-fix
source snapshot to passing portable tests and a controlled model comparison.
`READ_SLOTS=4` reserves four complete burst buffers and ordered row/word
metadata. Issue advances independently of retirement; storage remains owned
until bank delivery and terminal completion finish. The qualified image's
macrotile load/compute/store schedule was serial. The default build is one
read; the selectable overlap source uses the same one/four-read primitive
with independent load and store operation contexts.

The portable evidence includes 20 AXI primitive tests, 56 tile tests, 80
[packet/core tests](../results/ddr_gemm/read4/host_geometry/core/record.json)
across both read depths and all four P/T builds, eight
P8/T32 job tests and four diagnostic compatibility tests. Separate row and
build/programmer tests pass. External-fault tests retain held AXI requests,
cancel never-offered reads, drain accepted responses, exclude host memory
commands and preserve the original error and frozen counters. Two deliberate
read-stop defects are detected by the permanent regression. The registered-response core
tests additionally check calibration loss at read/write completion and read
capture, stable empty error replies and retained memory ownership.
The host-geometry revision adds an independently enumerated page-boundary
sweep with held AR/AW/W channels, complete data and guard checks.

The controlled P8/T32 behavioral helper sequence takes 12,343 / 9,795 cycles
for READ1 / READ4 on 32x32x256: 1.260x. Compute and C-store cycles and all
traffic match; 2,548 saved cycles belong to input loading. This is not the
full register JOB_CYCLES measurement or a physical speedup.

## Overlap implementation

The first prerequisite is now [verified in portable simulation](../results/tile_overlap_ports/README.md).
`CONCURRENT_PORTS=1` permits disjoint operand loads and completed-result reads
while a local job computes. Both buffer namespaces are tested independently,
and pending/stalled responses retain ownership. Twelve concurrent-port tests
pass across all four geometries: 36 jobs and 6,817 computed outputs checked,
with full result-matrix readbacks. The default serial engine passes its eight
tests/164 jobs, and P8/T32 four-read packet integration passes ten tests/eight
jobs. Three private ownership defects are detected. The original serial hierarchy selects
`CONCURRENT_PORTS=0` and rejects MODE=1. The selectable overlap build enables
these ports in the now board-qualified image `0x2c680af7`.

The [independent DMA prerequisite](../results/tile_dma_duplex/README.md) is
also verified. A fixed operand reader and C writer share the existing burst
engine while owning independent descriptors and held completions. Forty
tests pass across both read depths and all four geometries: 48 fully checked
stored matrices and 12,728 outputs. Each configuration exercises simultaneous
load/compute/store, full read-credit occupancy and shared fault draining with
stalled offers. Seven serial DMA tests and ten packet/core tests also pass.
The default hierarchy retains the serial adapter. The selectable hierarchy
connects the duplex wrapper to the tagged scheduler and validated job shell;
its physical qualification is separate from this prerequisite checkpoint.

The internal [tagged scheduler](tile-scheduler.md) now connects those stages
in portable simulation. Its [saved checkpoint](../results/tile_scheduler/README.md)
passes 24 tests across all eight P/T/read-depth configurations: 112 complete
jobs, 71,632 checked outputs and 336 macrotiles. Both modes use identical
input bytes and traffic. Directed stalls exercise different input/result IDs,
retain both completed C sets and prevent another launch until output space
is free. All configurations exercise load/compute/store together. Faults drain
accepted work and clear prior sticky success. The maximum descriptor checks
admission/count width only; it is not a completed maximum-shape job.

The [integrated overlap controller](ddr-overlap.md) now provides descriptor
validation, actual START acceptance, host exclusion, job IDs, watchdog,
calibration faults and frozen counters. `ENABLE_OVERLAP=1` selects this path;
VERSION=0x200 identifies its selectable MODE=0/1 contract. JOB_CYCLES retains
the actual final successful B timestamp, before scheduler DONE. The host,
build and programmer recognize the new identity and keep the original serial
path available. Its [integrated portable regression](../results/ddr_overlap/README.md)
passes 154 tests, 1,056 complete jobs and 322,799 compared outputs. The 800
seeded shell jobs supplement directed fault and packet tests. Windows and
Linux each pass 77 host/build/programmer/board-runner software tests.
This initial portable checkpoint predates the fault-control timing correction.
Its [first route](../results/ddr_overlap/initial_timing/README.md),
`0x2dd68757`, fits at 7,008/8,150 slices but misses setup by 2.029 ns.
The worst path is diagnostic-code selection into wide control enables.
The correction separates scalar detection from that payload without adding a
cycle. Its [fresh portable regression](../results/ddr_overlap/timing_predicate/README.md)
again passes all 154 tests, 1,056 jobs and 322,799 outputs; Windows and Linux
each pass the 77 software checks. The
[corrected route](../results/ddr_overlap/timing_predicate/initial_timing/README.md),
`0x36ffc81c`, reduces slice occupancy to 80.64% and the setup shortfall to
0.237 ns at 14 endpoints. The remaining path traverses burst-response metadata
and host progress/watchdog logic. An isolated
[post-route physical optimization](../results/ddr_overlap/timing_predicate/postroute_physopt/README.md)
passes setup/hold with +0.043/+0.020 ns margins. The production overlap flow
now includes the managed `phys_opt_design (Post-Route)` step with
`AggressiveExplore`; its final reports and gates assess that checkpoint.
Build `0x2c680af7` passes the [fresh route gate](../results/ddr_overlap/timing_predicate/final_build/route_probe/README.md)
at 100 MHz with +0.038/+0.016 ns setup/hold slack and all ten bus-skew checks.
Its [vendor integration](../results/ddr_overlap/timing_predicate/final_build/vendor/README.md)
passes all three framed-UART jobs, 49 outputs, guard checks and traffic totals
through SmartConnect, MIG and the unchanged DDR2 model. The simulator exits
zero after its final PASS. The [production bitstream and own-checkpoint review](../results/ddr_overlap/timing_predicate/final_build/routed/README.md)
are complete, with actual pipeline and Vivado exits zero. The image uses
17,030 LUTs, 20,874 FFs, 75 DSPs and 20 RAMB36 equivalents; 6,670 of 8,150
slices are occupied (81.84%). Clock definitions, CDC findings and reset
exceptions match the qualified serial build. Vendor reset and DSP pipelining
advisories remain recorded; passing implementation does not establish warm-reset
operation. After a reported fresh OFF/ON, the matching image was programmed
successfully and [both 48-job plans and 30 matched pairs](../results/ddr_overlap/timing_predicate/final_build/board/README.md)
passed with zero transport retries. Complete matched output/allocation snapshots
and the captured UART stream were independently replayed before publication.

The same image passes its [maximum-size comparison](../results/ddr_overlap/timing_predicate/final_build/maximum/README.md)
and [30.52-minute mixed run](../results/ddr_overlap/timing_predicate/final_build/endurance/README.md).
The latter checks 368 jobs, 342,286 outputs and both modes with zero transport
retries. It is a continuous host-paced repeatability check; no raw output-byte
or UART replay is claimed for that run.

## Measured development release

The 1 Mbaud T32 shape grid, both maximum-size modes, fresh sustained run,
T8 serial baseline and controlled comparison are complete. The
[verification summary](verification.md) and [FIFO/scheduler proofs](../results/buffer_formal/README.md)
state coverage and proof boundaries. [The host API](host-gemm.md) supplies
the deterministic demo and an example accepting user-provided matrices.
All results remain bound to their recorded images; source changes require
fresh qualification.

The development contract remains VERSION=0x200. The original full v1 proposal
also requires a separately measured resident-array run, isolated read-only,
write-only and mixed DDR bandwidth, and resolution of the warm-reset boundary.
Job COMPUTE_CYCLES and accepted traffic counts do not substitute for those tests.

## Platform record and limits

JTAG detects the xc7a50t die. The operator identifies Nexys A7 / Artix-7 50T
CSG324 and ISSI IS43DR16640C DDR2. The complete FPGA suffix, DDR speed/temperature
suffix and physical PCB revision remain unrecorded. Generated IP configuration,
clocks, memory model and constraints are preserved with each build.

Current physical evidence uses a cold power-up with JP1 in JTAG mode. The
warm-reset DDR timing issue remains open. Sparse diagnostic checks do not
constitute full-memory or electrical-margin qualification. Simulator assertions
and detected mutations are not a formal proof of the complete accelerator.

This is a measured development release, rather than full-v1 compliance. Generated
Vivado projects, environments, waveforms and bitstreams stay under ignored
`build/`; repository sources and scripts regenerate them.
