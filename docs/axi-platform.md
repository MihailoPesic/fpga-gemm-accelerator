# AXI DDR2 platform bring-up

The AXI MIG flow is separate from the working BRAM preview and the historical
native-DDR project. It currently generates the DDR controller and runs its
vendor-model simulation. It does not yet connect GEMM to DDR or produce a new
board bitstream.

```text
Implemented vendor simulation:
AMD AXI traffic generator -> 128-bit AXI MIG -> DDR2 memory model

Next hardware integration:
64-bit burst engine @ 100 MHz -> SmartConnect -> 128-bit AXI MIG @ 50 MHz
                                                        |
                                                  Physical DDR2
```

## Configuration

`scripts/test_mig.py` derives its input from the tracked native `mig_a.prj`.
It preserves the memory timings, address mapping, clock inputs and all 46 DDR
pin assignments. It changes the module name and selects AXI with a 128-bit
data bus, 27-bit byte addresses, four ID bits, narrow-transfer support and
RD_PRI_REG arbitration. The configured memory size is 128 MiB.

The retained configuration is MT47H64M16HR-25E, a 16-bit physical data bus,
200 MHz memory clock and a 4:1 ratio, yielding a 50 MHz user clock. MIG accepts
separate 100 MHz system and 200 MHz reference clocks with no input buffers
inside the IP. A physical wrapper must supply those clocks and coordinated
resets; the simulation supplies ideal clock inputs.

The scripts check the generated XCI parameters, narrow-support parameter and
every generated DDR pin and I/O standard. AMD documents loading a modified
project configuration through
[XML_INPUT_FILE](https://docs.amd.com/r/en-US/ug911-vivado-migration/Configuring-AXI-MIG)
and recommends matching the AXI data width to the native application width
for [performance](https://docs.amd.com/r/en-US/ug586_7Series_MIS/AXI4-Slave-Interface-Parameters).

## Run and inspect

Validated toolchain: Vivado 2026.1 with Artix-7/MIG support and native Python.
The XCI checks use that version's JSON representation. Older XML XCI formats
are not handled by this runner.

```text
python scripts/test_mig.py
```

Use `--vivado PATH` for another installation location and `--build-dir PATH`
for a fresh generated project. An existing project can be rerun when its
derived memory configuration is unchanged. Generated vendor code, models,
logs and simulation binaries stay under `build/axi_mig/`.

Open `build/axi_mig/project/axi_mig.xpr` in Vivado to inspect MIG and the test.
The simulation top is `tb_mig_axi`; run behavioral simulation and enter
`run all` in the Tcl Console. This project is a simulation fixture. Its
No Buffer clock ports need a board wrapper before physical implementation.

The script adds the generated example files using Tcl file lists instead of
`open_example_project`: Vivado 2026.1's generated example script fails on an
unquoted project-file path when the repository directory contains spaces.
The generated vendor HDL and memory model are not patched.

## Acceptance and limits

The vendor test uses FAST calibration and runs traffic for 50 us after
calibration. `tb/vendor/tb_mig_axi.sv` additionally checks the 50 MHz user
clock, calibration/reset stability, successful R/B responses, nonzero traffic
in both directions and the vendor data-comparison flag. A pass requires both
the vendor and monitor success records; failures return a nonzero process exit.

The generated test configures BEGIN_ADDRESS=0, END_ADDRESS=0xFFF and
PRBS_EADDR_MASK_POS=0xFF000000. The PRBS mask leaves low address bits random,
so the END_ADDRESS value does not bound every generated address to 4 KiB.
The saved summary records observed minimum/maximum AR/AW starting addresses.
This is a smoke test, not exhaustive coverage of any memory range.

Narrow support is enabled in the controller, but the vendor traffic test does
not enable its narrow-transaction mode. Physical calibration, high-address
alias checks, byte strobes and the 64-to-128-bit clock/width bridge still need
integration tests. The portable [burst-engine tests](axi-burst.md) exercise
the custom master against an independent AXI RAM; they do not simulate DDR pins.
