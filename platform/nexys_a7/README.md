# Nexys A7-50T platform

The GEMM BRAM preview uses `gemm_preview_top.sv` and `preview.xdc`, with no MIG
or clock-wizard dependency. Build it with `python scripts/build_preview.py`;
see the [preview interface and board workflow](../../docs/preview.md).

The current baseline is the tracked GUI project and `.srcs` tree at the
repository root. [manifest.json](manifest.json) records the saved memory
configuration and historical bitstream identity. It is a platform inventory,
not a claim that the new GEMM board design exists.

`board.tcl` selects the exact board required by the saved XCI files. Set
`NEXYS_BOARD_REPO` if Vivado cannot find the installed board repository.
The native reconstruction flow is `scripts/create_project.tcl` and
`scripts/build.tcl`; generated/imported files go under `build/native_ddr`.

The original clock wizard generates 100/200 MHz from the board's 100 MHz
oscillator. Native MIG uses a 200 MHz memory clock and 50 MHz, 128-bit UI.
The old UART bridge runs at 921600 baud in the UI domain. The target GEMM
master is instead 64-bit AXI at 100 MHz, with vendor width/clock conversion
to a separately generated AXI MIG. That transition is not implemented yet.

Two source repairs were applied after the July build: remove the duplicate primary clock
from the board XDC (the clock wizard owns it), and mark the UART RX synchronizer
ASYNC_REG. Synthesis checks the clock count. These source changes have not
been revalidated on the physical board, and the old bitstream hash identifies
the earlier source revision rather than these changes.

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

Remaining platform gates: physical board revision/marking, board-file commit
or immutable content identity, cold-reset/CDC review, repaired-source board test,
MIG example memory simulation/test, new AXI clock/width configuration and
full timing/constraint review. The older sparse memory test is not a full
128 MiB memory qualification or a GEMM correctness test.

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
