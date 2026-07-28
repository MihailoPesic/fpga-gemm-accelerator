# nexys-accelerator

Hardware accelerator for the **Digilent Nexys A7-50T** (Xilinx Artix-7,
`xc7a50ticsg324-1L`), built with Vivado 2026.1.

## Repository model

The Vivado `.xpr` is a **build artifact and is not tracked**. It embeds absolute
paths and a machine-local board-store location, so it does not survive a clone.
`scripts/create_project.tcl` is the single source of truth for project
configuration; run it to materialise a working project from the tracked sources.

```
.
├── src/
│   ├── hdl/            design RTL (.v .sv .vhd) — tracked
│   ├── ip/             Vivado IP configuration (.xci .bd) — tracked
│   └── constraints/    .xdc — tracked
├── sim/                testbenches — tracked
├── scripts/
│   ├── create_project.tcl
│   └── build.tcl
├── doc/
└── vivado/             generated project — ignored
```

## Quick start

```sh
vivado -mode gui -source scripts/create_project.tcl
```

Headless build to bitstream:

```sh
vivado -mode batch -source scripts/build.tcl -tclargs -jobs 8
```

`build.tcl` creates the project on first run, then synthesises, implements,
writes the bitstream, and **exits non-zero if timing is not met**.

Useful flags:

| Command | Effect |
| --- | --- |
| `create_project.tcl -tclargs -force` | replace an existing generated project |
| `build.tcl -tclargs -synth-only` | stop after synthesis |
| `build.tcl -tclargs -no-bitstream` | implement, skip `write_bitstream` |

## Working rules

**Add sources with "Copy sources into project" UNCHECKED.** Files must stay in
`src/` so git tracks them. If a file ends up in `<project>.srcs/` it is outside
the tracked tree — `.gitignore` deliberately leaves `*.srcs/` visible so git
reports it as untracked and you catch the mistake.

**Re-run `create_project.tcl` after adding files** — or just add them to `src/`
and regenerate. New `.v`/`.sv`/`.vhd`/`.xdc`/`.xci` files are picked up by glob,
so the script needs no edit for ordinary additions.

**Commit `.xci`, never the generated IP output.** IP products land in
`*.gen/` and are reproducible from the `.xci`.

## Constraints

`src/constraints/Nexys-A7-50T-Master.xdc` is Digilent's master constraint file,
shipped fully commented out. Uncomment the pins you use and rename the ports to
match your top-level signal names.

Board resources: 100 MHz clock (E3), 16 switches, 16 LEDs, 5 buttons, 2 RGB
LEDs, 8-digit 7-segment display, USB-UART, Ethernet PHY, USB-HID host, micro-SD,
ADXL362 accelerometer (SPI), QSPI flash, temperature sensor, microphone, mono
audio out, 128 MiB DDR2 (MT47H64M16, via MIG 7-series), Pmod JA/JB/JC/JD and
JXADC.

## Device budget (XC7A50T)

| Resource | Available |
| --- | --- |
| LUT | 32,600 |
| FF | 65,200 |
| DSP48E1 | 120 |
| BRAM36 | 75 (2.7 Mb) |

## Toolchain

Vivado 2026.1 and Vitis 2026.1. The board files are installed per-user, not with
Vivado; `create_project.tcl` fetches them from the Xilinx Board Store if they are
missing, or install manually via **Tools → XHub Stores → Board Store → Digilent →
Nexys A7-50T**.
