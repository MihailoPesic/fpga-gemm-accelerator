# Nexys A7-50T platform

The 1 Mbaud selectable DDR2 GEMM image `0x9d4beb4d` passes 30 matched
serial/overlap pairs, with all 3,932,160 useful outputs checked. At
256x256x256 it measures 11.298 useful GOPS with overlap and a 2.495x paired
speedup over serial mode. Its [board record](../../results/ddr_overlap/release_1mbaud/board/t32/dense/README.md)
and [routed implementation](../../results/ddr_overlap/release_1mbaud/t32/README.md)
retain the exact image and source identities. Earlier checkpoints are saved
in the [DDR GEMM evidence](../../results/ddr_gemm/README.md) and the
[115200-baud selectable record](../../results/ddr_overlap/timing_predicate/final_build/board/README.md).
The new T32 route passes 100 MHz with +0.017/+0.017 ns setup/hold slack;
its narrow control-path margin and high slice occupancy remain documented.

The GEMM BRAM preview uses `gemm_preview_top.sv` and `preview.xdc`, with no MIG
or clock-wizard dependency. Build it with `python scripts/build_preview.py`;
see the [preview interface and board workflow](../../docs/preview.md).
The September 30 preview bitstream passes routed timing at 100 MHz and full
GEMM output comparisons on the board; see the [saved evidence](../../results/preview/README.md).

The historical native-DDR baseline is the tracked GUI project and `.srcs` tree at the
repository root. [manifest.json](manifest.json) records the saved memory
configuration, historical bitstream identity and subsequent platform checks.
The preview and AXI DDR diagnostic each have a separate generated build manifest.

`board.tcl` selects the exact board required by the saved XCI files. Set
`NEXYS_BOARD_REPO` if Vivado cannot find the installed board repository.
The native reconstruction flow is `scripts/create_project.tcl` and
`scripts/build.tcl`; generated/imported files go under `build/native_ddr`.

The original clock wizard generates 100/200 MHz from the board's 100 MHz
oscillator. Native MIG uses a 200 MHz memory clock and 50 MHz, 128-bit UI.
The old UART bridge runs at 921600 baud in the UI domain.

The saved MIG preset/model is Micron MT47H64M16HR-25E. The operator reports
ISSI IS43DR16640C on the physical board; its speed/temperature suffix remains
unreadable. The [DDR2 compatibility record](../../docs/ddr-memory-compatibility.md)
separates these identities and reviews the configured command timings.

The implemented [AXI platform wrapper](axi_ddr_platform.sv) connects a 64-bit,
100 MHz AXI master through SmartConnect to a separately generated 128-bit,
50 MHz AXI MIG. [The DDR diagnostic top](gemm_ddr_diag_top.sv) exercises this
path with the custom burst engine and UART control. Its implementation passes
100 MHz routed timing with +0.642 ns setup slack and +0.027 ns hold slack.
Three seeded board runs passed after an operator-confirmed cold power-up.
Each run initializes 4,096 bytes across 16 selected regions, checks their contents
and masked writes, and completes 1,024 read beats and 648 write beats.

See the [platform configuration](../../docs/axi-platform.md),
[diagnostic build and board workflow](../../docs/ddr-diagnostic.md) and
[saved verification evidence](../../results/ddr_platform/README.md).
These results cover the bounded diagnostic. The
[tile DMA path](../../docs/tile-dma.md) is tested separately against AXI RAM;
its serial descriptor controller and registers are verified separately. The
[packet subsystem](../../docs/ddr-core.md) now connects those modules with
framed host commands and shared memory ownership against AXI RAM.
The [complete UART/GEMM vendor test](../../results/ddr_gemm/vendor/summary.json)
now passes two jobs with all 16 outputs compared. The matching production
bitstream passes setup/hold at +0.097/+0.014 ns, and
[DDR GEMM board qualification](../../results/ddr_gemm/board/README.md) passes
48 jobs with all 49,593 outputs checked after fresh power-up. The complete
image has its own [implementation identity](../../results/ddr_gemm/routed/README.md).
Warm-reset clock/CKE behavior remains unqualified,
and the diagnostic is neither a full memory sweep nor a bandwidth measurement.

Two source repairs were applied after the July build: remove the duplicate primary clock
from the board XDC (the clock wizard owns it), and mark the UART RX synchronizer
ASYNC_REG. Synthesis checks the clock count. The historical native design has
not been rebuilt and board-tested with those repairs; its old bitstream hash
identifies the earlier source revision. The newer AXI diagnostic and GEMM
images have their own source hashes and board evidence linked above.

The fully commented [Digilent master XDC](reference/Nexys-A7-50T-Master.xdc)
is retained for pin lookup; its [upstream source](https://github.com/Digilent/digilent-xdc/blob/master/Nexys-A7-50T-Master.xdc)
is maintained by Digilent. It is not an active constraint file. DDR pins and
timing come from the tracked MIG configuration and its generated constraints;
unused UCF/XDC pin-map copies and the old reference MIG project are omitted.

## Retest the existing board baseline

This checks the historical UART/DDR2 design, not GEMM. The local July bitstream
is ignored build output and is not distributed in a clean clone.

1. Connect a USB data cable to J6 (USB-UART/JTAG). If powering over USB, select
   USB on JP3; turn on SW16. See the [Digilent board manual](https://digilent.com/reference/_media/reference/programmable-logic/nexys-a7/nexys-a7_rm.pdf).
2. In Vivado, open **Hardware Manager -> Open Target -> Auto Connect**. Confirm
   that the detected FPGA is `xc7a50t` before selecting the saved 50T image.
3. Right-click the device, choose **Program Device**, and select
   `accelerator nexys.runs/impl_1/top.bit` relative to this repository. Leave
   the probes file empty. Its SHA-256 must match `historical_bitstream_sha256`
   in [manifest.json](manifest.json). [AMD programming reference](https://docs.amd.com/r/en-US/ug835-vivado-tcl-commands/program_hw_devices).
4. Check LED15 on (clock lock), LED0 on (DDR calibration) and LED1 blinking
   (UI clock heartbeat). These signals are assigned in the retained `top.sv`.
5. Find the board's USB Serial Port in Windows Device Manager. With Windows
   Python and pyserial installed, run `python host/legacy/nexys.py COMx` from
   the repository root, substituting that port. The test writes known patterns
   to DDR and checks adjacent and scattered locations; it is not a full memory test.

For a clean clone, reconstruct using the scripts documented in
[testing.md](../../docs/testing.md); a new build must pass its report checks
before programming. Do not regenerate IP merely to program the saved image.

The operator confirmed the Nexys A7 / 50T / CSG324 marking; the full device
speed/temperature suffix and physical board revision remain unrecorded.
The historical native flow still needs a repaired-source board test and an
immutable board-file identity. Its sparse memory test does not qualify the full
128 MiB or validate GEMM. The newer AXI diagnostic has separate routed timing,
clock/reset review and board evidence linked above; warm-reset qualification,
broader memory coverage remain outstanding. Matrix DMA now has the separate
GEMM board record linked above.

On September 30, the saved July bitstream was programmed on the detected
`xc7a50t`. Clock-lock/calibration/heartbeat LEDs were observed on the board, and the
native host test passed through COM11: ping, one word, eight adjacent words
and seven scattered words. See the [record](../../results/native_board/2026-09-30.json)
and [output](../../results/native_board/2026-09-30.txt). This retests the historical
bitstream; it does not validate the later source repairs.

With Python installed on Windows, create a local host environment and run the
test using the board's detected serial port (replace `COMx`):

```powershell
python -m venv build/host-venv
& ./build/host-venv/Scripts/python.exe -m pip install -r requirements-host.txt
& ./build/host-venv/Scripts/python.exe host/legacy/nexys.py COMx
```

The saved COM11 measurement identifies the original test setup; port numbering
is machine-specific.
