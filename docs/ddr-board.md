# DDR GEMM board integration

[`gemm_ddr_top`](../platform/nexys_a7/gemm_ddr_top.sv) connects the production
UART receiver/transmitter to [`gemm_ddr_core`](../rtl/control/gemm_ddr_core.sv)
and the existing [AXI DDR platform](axi-platform.md). The portable core, memory
layout, packet protocol and register semantics are shared with the AXI RAM
regression. `ENABLE_OVERLAP=1` selects VERSION=0x200 and MODE=0/1; the
original serial hierarchy retains VERSION=0x100 and MODE=0 only.

The current [P8/T32 image](../results/ddr_overlap/timing_predicate/final_build/routed/README.md)
`0x2c680af7` passes vendor simulation and routed 100 MHz implementation gates.
Its [cold-start board qualification](../results/ddr_overlap/timing_predicate/final_build/board/README.md)
passes 48 jobs per mode and 30 matched pairs, with all 344,946 useful outputs
checked. At 64x64x256, overlap measures median 25,130 cycles / 8.345 useful
GOPS and 1.837x paired speedup over serial mode on the same image. These
job intervals include DDR tile loads and result completion; UART time is separate.
The same image now passes its [maximum-shape board check](../results/ddr_overlap/timing_predicate/final_build/maximum/README.md):
all 1,048,576 outputs at 1024x1024x256. Its [sustained run](../results/ddr_overlap/timing_predicate/final_build/endurance/README.md)
passes 368 mixed jobs over 30.52 continuous host-paced minutes in both modes.
Qualification of the separate 1 Mbaud images remains a release gate.

The preceding [serial P8/T32 four-read image](../results/ddr_gemm/read4/host_geometry/routed/README.md)
passes all 100 MHz implementation gates and the complete UART/vendor-DDR test.
Its [physical qualification](../results/ddr_gemm/read4/host_geometry/board/README.md)
passes 48 jobs with 49,593 outputs and full input/padding/guard checks. Dense
32x32x256 takes median 11,576.5 cycles, or 4.529 useful GOPS including DDR job
transfers. UART movement is separate. The saved one-read P8 median is
21,281.5 cycles: this subsystem revision is 1.83834x faster on the same workload.

The earlier [one-read P8/T32 image](../results/ddr_gemm/p8_serial/routed/README.md) passes all
100 MHz implementation gates and the complete UART/vendor-DDR test. Its
[physical qualification](../results/ddr_gemm/p8_serial/board/README.md) passes 48 jobs,
with 49,593 outputs and full input/padding/guard checks. The dense 32x32x256
case measures median 21,281.5 cycles, or 2.464 useful GOPS including DDR job
transfers; UART movement is separate. The [controlled P4/P8 comparison](../results/ddr_gemm/p8_scaling/README.md)
uses the same host and RTL sources, seven matrix pairs and 100 MHz clock.
P4's dense median is 34,103 cycles, giving a 1.60x speedup. The full
overlap release remains under development.

```text
Laptop Python host
       |
   USB-UART, 8N1
       |
UART RX/TX -> COBS/CRC/replay -> registers + host memory commands
                                      |
                         validated serial/overlap job controller
                                      |
                         scheduling + DMA + A/BT/C banks
                                      |
                               P x P systolic array

Host memory and tile DMA -> shared 64-bit AXI burst engine @ 100 MHz
                                      |
                           SmartConnect width/clock conversion
                                      |
                             128-bit MIG AXI @ 50 MHz
                                      |
                              DDR2, x16 @ 200 MHz
```

The platform supplies the generated core clock, coordinated reset and
synchronized calibration status. The wrapper adds UART and four LED pins;
it does not create another primary clock or independently reset the AXI
master. MIG generation preserves the saved memory settings and all 46 DDR
pin assignments.

## Build and inspect

With native Python and Vivado 2026.1:

```text
python scripts/build_ddr_gemm.py --stage sim --overlap --p 8 --read-slots 4 --build-dir build/gemm_p8_current
python scripts/build_ddr_gemm.py --stage bitstream --overlap --p 8 --read-slots 4 --build-dir build/gemm_p8_current
```

The Make targets `sim-gemm-ddr` and `ddr-gemm-bitstream` retain the P4/one-read
defaults; the explicit commands above select P8/four-read with MODE=0/1.
Generated
projects, vendor models, logs, routed reports and bitstreams stay under the
selected ignored build directory. After both batch stages finish, open
`build/gemm_p8_current/project/axi_platform.xpr` in Vivado to
inspect the board top, block design and `tb_ddr_gemm` simulation. Routed timing
and resource reports are also saved as text beside `build.json`.
The existing qualified image's project is
`build/gemm_p8_overlap_final/project/axi_platform.xpr`. Preserve its
archive and inspect its saved reports for build `0x2c680af7`; build changed
source in a fresh directory. Source files referenced by the project can change
with the workspace, while its old routed reports retain their recorded identity.
During a batch run, inspect `prepare_console.txt`, `sim_console.txt` or
`bitstream_console.txt` without opening its project in another Vivado session.
The project bytes are sealed with the test inputs; GUI changes can invalidate
the next build stage.
A successful simulation with matching source and generated
configuration identities is required before bitstream generation. Routing
must pass setup, hold, pulse-width, bus-skew, clock, CDC and constraint checks.
An old manifest cannot qualify changed inputs.

Omit `--overlap` from both commands to build the original serial hierarchy.
The implementation run includes post-route `AggressiveExplore` optimization
before the final checkpoint, reports and bitstream gates. The intermediate
route report remains available; setup/hold signoff uses the optimized result.
Both modes then share one VERSION=0x200 image. Its build identity includes
the Tcl flow, so changing optimization invalidates an earlier image seal.

Batch simulation defaults to `--sim-debug off`. To inspect internal waveforms,
use `--sim-debug typical --build-dir build/gemm_debug` for a separate generated
project. The debug setting is part of the build identity; changing it requires
a fresh simulation rather than reusing the batch build's evidence.
`--build-dir` selects another ignored output directory when changing sources
or settings. Use the same directory and settings for its simulation and
bitstream stages; retain earlier results under their original identities.

The physical UART build initially uses 115200 baud. The vendor fixture uses
a faster exact-divisor simulation baud to bound runtime. It still drives and
decodes actual UART pins; this does not qualify the faster rate on a board.
The generated DDR2 model is unmodified. FAST calibration and ideal PCB delays
make this a functional integration test, not physical memory timing evidence.

## Board procedure

If the qualified P8 image is already programmed and ready, run it directly:

```text
python -m host.gemm --port COM11 --manifest build/gemm_p8_overlap_final/build.json --mode 1 --m 5 --n 3 --k 9 --repeats 3 --retries 0 --output build/gemm_p8_overlap_final/demo_small
```

This reloads inputs, compares every result and checks padding/guards. It needs
no GUI project or new bitstream. Use a fresh output directory to preserve
earlier demo results. The ignored local archive contains the qualified
bitstream and manifest; a clean clone must build its own image. Keep this
archive when building changed sources in another directory.

The configured Micron MIG preset is distinct from the partially identified
ISSI chip reported on this board. Check the [physical memory identification
and compatibility record](ddr-memory-compatibility.md) before treating a
qualified bitstream as a complete platform qualification.

Use a qualified build manifest and its matching bitstream. Keep JP1 in JTAG
mode. Power the board OFF and ON before loading this image through J6; the
previous diagnostic's warm-reset qualification remains unresolved. An older
image must not initialize DDR first. Reprogramming a powered DDR controller
is also a warm-controller event. Every reset invalidates matrix residency.

LED0 indicates calibrated DDR; LED1 indicates a matrix job or host memory
transfer in progress; LED2 is sticky successful job completion; LED3 reports
an error. After initial calibration and before commands, LED0 should be on
and the other three off.

After the cold power-up, program the qualified image and run a small test:

```text
python scripts/program_ddr_gemm.py --manifest build/gemm_p8_current/build.json
python -m host.gemm --port COM11 --manifest build/gemm_p8_current/build.json --mode 1 --m 5 --n 3 --k 9 --repeats 3 --output build/gemm_p8_current/hw_small
```

These cold-start commands use the fresh P8 build directory from the build
procedure above; substitute `build/gemm_p8_overlap_final/build.json` for the
preserved qualified local image. Replace COM11 with the board's current serial port. Close other serial terminals
before the host test. The generated project and bitstream cannot be substituted
with the old native design, BRAM preview or DDR diagnostic.

Before launching Vivado, the programmer verifies the selected bitstream hash,
the saved PASS simulation and its source, build and generated platform identities,
and every routed-report hash. Keep `simulation.json` and all reports beside
`build.json` when archiving a qualified image. Programming an archived image
does not require the current checkout to match its original sources.

The [host library](host-gemm.md) checks ID/version, geometry, BUILD_ID and
core clock before loading inputs. It compares every result and records frozen
DDR-job counters separately from packing and UART movement. The selected
manifest also verifies the local bitstream file's SHA-256; UART reports the
build identity, not a hash readback of FPGA configuration.

Start with signed extrema and odd/non-square shapes. Then test dimensions
beyond T with the same bitstream. Preserve failures and raw counters before
resetting. Physical correctness, throughput and repeatability require board
results; portable and vendor simulation do not supply those measurements.

The bounded P4/P8 T32 qualification is reproducible with one command after
programming the qualified image:

```text
python scripts/hw_test_ddr_gemm.py --port COM11 --manifest build/gemm/build.json
```

The Make equivalent is `make hw-test PORT=COM11 MANIFEST=build/gemm/build.json`.
P4 is the default. For a qualified P8 image, select the geometry explicitly:

```text
python scripts/hw_test_ddr_gemm.py --p 8 --port COM11 --manifest build/gemm_p8_read4_host_geometry/build.json --retries 0
make hw-test GEMM_P=8 PORT=COM11 MANIFEST=build/gemm_p8_read4_host_geometry/build.json
```

Both plans use identical matrices, seeds, shapes and repetitions. Only the
declared P changes. The manifest and hardware identity must match that geometry;
an incompatible manifest fails before opening COM. Counter checking uses
`ceil(M/P) * ceil(N/P) * (K + 3*P - 1)` for active compute schedules.

Use the manifest for the selected build directory. The runner checks its
bitstream, simulation and physical reports before opening the serial port.
Results default to a new `hardware_qualification/<UTC timestamp>` directory
beside the manifest, with combined and per-case JSON/CSV records. An existing
nonempty output directory is rejected.

| Case | M x N x K | Runs |
| --- | --- | ---: |
| -128 x -128 scalar | 1 x 1 x 1 | 3 |
| Maximum positive/negative signed reductions | 1 x 2 x 256 | 3 |
| Odd output/reduction tails | 5 x 3 x 9 | 3 |
| Column macrotile boundary | 31 x 33 x 17 | 3 |
| Multiple macrotiles, maximum K | 33 x 35 x 256 | 3 |
| Asymmetric shape, K tail | 65 x 63 x 255 | 3 |
| Dense DDR-resident repetitions | 32 x 32 x 256 | 30 |

This plan compares all 49,593 outputs across 48 jobs, plus complete input
allocations, output padding and external guards after every job. Each case
uploads once and reuses its inputs for repetitions; each next case reloads
inputs and sentinels. Frozen job counters include DDR tile loads and result
completion. Packing, UART movement and validation have separate time records.
The first mismatch or hardware/transport error stops the plan and retains
completed results. This is a bounded correctness and performance checkpoint,
not full-memory coverage or the required 30-minute endurance test.
