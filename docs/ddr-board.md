# DDR GEMM board integration

[`gemm_ddr_top`](../platform/nexys_a7/gemm_ddr_top.sv) connects the production
UART receiver/transmitter to [`gemm_ddr_core`](../rtl/control/gemm_ddr_core.sv)
and the existing [AXI DDR platform](axi-platform.md). The portable core, memory
layout, packet protocol and register semantics are shared with the AXI RAM
regression. `ENABLE_OVERLAP=1` selects VERSION=0x200 and MODE=0/1; the
original serial hierarchy retains VERSION=0x100 and MODE=0 only.

The measured [1 Mbaud P8/T32 image](../results/ddr_overlap/release_1mbaud/README.md)
`0x9d4beb4d` passes vendor simulation, routed 100 MHz checks, both scheduling
modes, the full shape grid and cold-start board repeatability tests.
[Status](status.md) records the results and remaining full-v1 limits.
The [evidence index](README.md) retains earlier P4/P8 and 115200-baud checkpoints
under their original source and image identities.

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

## Build from a clean clone

Run from the repository root with native Python, Git and Vivado 2026.1 with
Artix-7 support. Install the host dependencies, then build P8/T32 with four
read slots, selectable MODE=0/1 and a 1 Mbaud UART:

```text
python -m pip install -r requirements-host.txt
python scripts/build_ddr_gemm.py --stage sim --overlap --p 8 --t 32 --read-slots 4 --baud 1000000 --build-dir build/gemm_current
python scripts/build_ddr_gemm.py --stage bitstream --overlap --p 8 --t 32 --read-slots 4 --baud 1000000 --build-dir build/gemm_current
```

With GNU Make, `make bitstream PYTHON=python` runs the same two stages.
Its `RELEASE_BUILD` defaults to `build/gemm_current`; set another fresh directory
when changing sources or settings. Set `VIVADO` for Make, or `--vivado` for
the Python scripts, if Vivado is not on PATH. The older `sim-gemm-ddr` and
`ddr-gemm-bitstream` targets retain serial P4/one-read/115200-baud defaults.

Generated projects, vendor models, logs, routed reports and bitstreams stay under the
selected ignored build directory. After both batch stages finish, open
`build/gemm_current/project/axi_platform.xpr` in Vivado to
inspect the board top, block design and `tb_ddr_gemm` simulation. Routed timing
and resource reports are also saved as text beside `build.json`.
The preserved qualified image's project is
`build/gemm_release_p8_t32_1mbaud/project/axi_platform.xpr`. Preserve its
archive and inspect its saved reports for build `0x9d4beb4d`; build changed
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

With `--overlap`, MODE=0/1 share one VERSION=0x200 image. Omit `--overlap`
from both commands to build the original serial VERSION=0x100 hierarchy,
which supports MODE=0 only.
The implementation run includes post-route `AggressiveExplore` optimization
before the final checkpoint, reports and bitstream gates. The intermediate
route report remains available; setup/hold signoff uses the optimized result.
The build identity includes the Tcl flow, so changing optimization invalidates
an earlier image seal.

Batch simulation defaults to `--sim-debug off`. To inspect internal waveforms,
use `--sim-debug typical --build-dir build/gemm_debug` for a separate generated
project. The debug setting is part of the build identity; changing it requires
a fresh simulation rather than reusing the batch build's evidence.
`--build-dir` selects another ignored output directory when changing sources
or settings. Use the same directory and settings for its simulation and
bitstream stages; retain earlier results under their original identities.

The commands above select a physical 1 Mbaud UART; the script's default baud
remains 115200 for earlier builds. The vendor fixture uses
a faster exact-divisor simulation baud to bound runtime. It still drives and
decodes actual UART pins; this does not qualify the faster rate on a board.
The generated DDR2 model is unmodified. FAST calibration and ideal PCB delays
make this a functional integration test, not physical memory timing evidence.

## Program and run the new build

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

Close other serial terminals. After the cold power-up, program the matching
new build and run a small test:

```text
python scripts/program_ddr_gemm.py --manifest build/gemm_current/build.json
python -m host.gemm --port COM11 --manifest build/gemm_current/build.json --mode 1 --m 5 --n 3 --k 9 --repeats 3 --retries 0 --output build/my_gemm_demo
```

These commands use the same build directory as the clean-clone procedure.
Replace COM11 with the board's current serial port. Choose a fresh output
directory; the demo CLI overwrites JSON/CSV in its selected directory.
With GNU Make, `make demo PORT=COM11 PYTHON=python RELEASE_OUTPUT=build/my_gemm_demo`
uses the same default manifest. A `RELEASE_BUILD` override also changes that
default; set `MANIFEST` explicitly to select a different archived build.
The generated project and bitstream cannot be substituted
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

For the current P8 release, `make bench` runs the 16-shape benchmark grid;
`make hw-release` also runs maximum-size and continuous repeatability checks.
Both require `PORT`, native `PYTHON` and a fresh `RELEASE_OUTPUT`, and default
to `build/gemm_current/build.json`. They never program or reset the board.
See [testing](testing.md#release-board-measurements) for the Python commands
and measurement boundaries.

## Use the preserved image

If image `0x9d4beb4d` is already programmed and ready, with its matching ignored
local archive present, run:

```text
python -m host.gemm --port COM11 --manifest build/gemm_release_p8_t32_1mbaud/build.json --mode 1 --m 5 --n 3 --k 9 --repeats 3 --retries 0 --output build/my_gemm_demo
```

This reloads inputs and checks every result and guard byte. It needs no GUI
project or new bitstream. A clean clone does not contain that archive; use the
build procedure above. Keep qualified archives when building changed sources
in another directory. Power-off loses a JTAG-loaded image; program its archived
manifest only after another cold OFF/ON, then reload all inputs.

## Legacy 115200-baud qualification

`hw_test_ddr_gemm.py` retains the bounded P4/P8 T32 **115200-baud** plan.
It rejects the current 1 Mbaud image. To reproduce this earlier checkpoint,
program its matching archived image after a cold start, then run:

```text
python scripts/hw_test_ddr_gemm.py --port COM11 --manifest build/gemm/build.json
```

The Make equivalent is `make hw-test PORT=COM11 LEGACY_MANIFEST=build/gemm/build.json`.
P4 is the default. For a qualified P8 image, select the geometry explicitly:

```text
python scripts/hw_test_ddr_gemm.py --p 8 --port COM11 --manifest build/gemm_p8_read4_host_geometry/build.json --retries 0
make hw-test GEMM_P=8 PORT=COM11 LEGACY_MANIFEST=build/gemm_p8_read4_host_geometry/build.json
```

`LEGACY_MANIFEST` defaults to `build/gemm/build.json`; an explicit command-line
`MANIFEST` remains supported for existing `hw-test` commands.

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
