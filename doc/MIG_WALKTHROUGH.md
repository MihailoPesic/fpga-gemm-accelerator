# MIG 7-Series Walkthrough — how to derive the settings, not just copy them

Values here are for the Nexys A7-50T. The **Determined by** column is what
transfers to any other board.

## Before opening the wizard

**Find the board vendor's reference MIG configuration first.** The wizard's
defaults are generic and will be wrong. For Digilent boards it ships inside the
XHub board package:

```
<board_store>/.../boards/Digilent/<board>/<ver>/<ver>/mig.prj
```

That XML is the authority on memory clock, memory part, Internal Vref and the
full pinout. On this board its defaults disagreed with the wizard on **four**
settings, two of which would have made the design silently wrong.

Other vendors: look for a reference design, a `.prj`, or the board manual's
memory chapter. If none exists, the DRAM datasheet plus the schematic are the
fallback.

---

## MIG pages

| Page / setting | Nexys A7-50T | Determined by |
|---|---|---|
| **User Options** | | |
| Output option | Create Design | always, unless verifying an edited pinout |
| Controllers | 1 | how many independent memory interfaces the board has |
| AXI4 interface | **OFF** | *your design*. ON only if you have an AXI master already — MicroBlaze, DMA, Zynq. Writing an AXI master by hand to reach DRAM is strictly more work than the native interface |
| **Pin Compatible FPGAs** | none | whether you plan to move to another device in the same package. Selecting some restricts pin choice for no gain otherwise |
| **Memory Selection** | DDR2 SDRAM | *the chip on the board* |
| **Controller Options** | | |
| Clock period | **5000 ps (200 MHz)** | *the PCB*, bounded by *silicon*. The wizard offers a range the FPGA can do; the vendor validates what the board's routing can do. Take the vendor's number, not the tool's minimum |
| PHY:controller ratio | **4:1** | the clock period restricts the choice. Sets `ui_clk = mem_clk / ratio` and UI width = `DQ_WIDTH x 2 x ratio` |
| Memory type | Components | *the board*: soldered chips vs a DIMM socket |
| Memory part | **MT47H64M16HR-25E** | *the chip*. Read the silkscreen or the vendor prj. Wrong part = wrong row/col count = phantom address pins and silent aliasing |
| Data width | **16** | *the chip(s)*: one x16 device here. Two x8 devices would also be 16 |
| ECC | Disabled | only available at 72-bit |
| Data mask | **checked** | *the PCB*: are the DM pins routed to the FPGA? |
| Bank machines | 4 | *your access pattern*. More helps random access, costs logic. Sequential streaming does not need many |
| Ordering | **Strict** | *your access pattern*. Sequential access has nothing to reorder, and Strict guarantees in-order read return, which makes the receiving logic a plain counter |
| **Memory Options** | | |
| Input clock period | **10000 ps (100 MHz)** | *what you can feed it*. The list is generated from the memory clock — only ratios its PLL can hit appear. If your oscillator frequency is absent, an MMCM must synthesise one that is present |
| Burst type | Sequential | *your addressing*. Interleaved is for cache-line fills |
| Output drive strength | Fullstrength | *the PCB* signal integrity |
| RTT / ODT | **75 ohms** | *the PCB* |
| Chip select pin | Enable | *the PCB*: is CS# routed, or tied low on the board? |
| Address mapping | **ROW_BANK_COLUMN** | *your access pattern*. ROW_BANK_COLUMN puts consecutive column blocks in different banks, so the controller can activate ahead and hide `tRC` — good for streaming. BANK_ROW_COLUMN keeps runs inside one bank. Performance only, never correctness |
| **FPGA Options** | | |
| System clock | **No Buffer** | *your clock topology*. Single-Ended / Differential = MIG instantiates the input buffer from a pin. No Buffer = you feed it an internal net. Two input buffers cannot share one pin, so if an MMCM already owns your clock pin, MIG must be No Buffer |
| Reference clock | **No Buffer** | same, plus: `Use System Clock` is legal only when the system clock is ~200 MHz |
| Reset polarity | **ACTIVE LOW** | *your preference*. Low pairs naturally with `mmcm_locked & button_n` |
| Debug signals | OFF | ON adds an ILA. Turn on only when calibration fails and you need the PHY stage flags |
| Internal Vref | **CHECKED** | *the PCB*: does it drive the bank's VREF pins to 0.9 V? If not, internal Vref is mandatory, not an optimisation. Legal at <= 800 Mbps |
| IO power reduction | ON | free |
| XADC instantiation | Enabled | *your design*: enable unless you instantiate XADC yourself, in which case MIG needs `device_temp_i` from you. MIG uses die temperature to keep read DQS centred |
| **Extended FPGA Options** | | |
| Internal termination | 50 Ohms | *the PCB* |
| **IO Planning** | **Fixed Pin Out** | always, for a real board. "New Design" lets MIG choose pins — only valid when you are still laying out the PCB |
| **Pin Selection** | Read XDC/UCF, then **Validate** | *the PCB*. Validate is the real check: DQ and DQS must sit in the correct byte lanes within a bank because the PHY hardwires byte groups to IO tiles |
| **System Signals** | all **No connect** | *your design*. No connect makes them module ports you wire yourself; assigning a pin sends them straight to a package ball. You want `init_calib_complete` as a signal, so your logic can gate on it |

**Sanity check before leaving Controller Options:** the Memory Details line must
match the datasheet. Here: `1Gb, x16, row:13, col:10, bank:3`. Wrong density or
row count means the wrong part is selected.

---

## Clocking Wizard

| Setting | Value | Determined by |
|---|---|---|
| Primitive | MMCM | PLL is cheaper but has fewer outputs and no phase shift |
| Input frequency | 100 MHz | *the board oscillator* |
| Input source | Single ended clock capable pin | *your topology*: whoever owns the physical pin |
| `clk_out1` | 100 MHz | *what MIG's Input Clock Period demands* |
| `clk_out2` | 200 MHz | *the silicon*: `IDELAYCTRL` needs 200 MHz +/- 10. Not negotiable, not a MIG preference |
| `locked` | on | needed to hold MIG in reset until clocks are valid |
| `reset` | off | nothing sensible to drive it with before the MMCM exists |

Check the **Actual** frequency column, not Requested. Integer dividers off one
VCO give exact values; anything else introduces error.

---

## The four rules worth remembering

1. **Find the vendor's reference config before touching the wizard.** Generic
   defaults are wrong on real boards, and two of the wrong ones fail silently.
2. **Take the validated clock, not the tool's maximum.** The tool knows the
   silicon; the vendor knows the PCB. Marginal calibration fails intermittently,
   which is the most expensive bug class there is.
3. **Test the controller alone before anything else exists.** One LED on
   `init_calib_complete` is a total assertion over the part, all pins, byte
   groups, Vref, termination, the reference clock and reset sequencing.
4. **`IDELAYCTRL` always needs 200 MHz.** If the board's oscillator is not
   200 MHz, an MMCM is mandatory, and that usually forces MIG's clocks to
   "No Buffer".

---

## Suggested session order

| | Step | Time |
|---|---|---|
| 1 | Install board files via XHub | 5 min |
| 2 | Install cable drivers (Linux: `install_drivers`, needs sudo) | 5 min |
| 3 | Open `mig.prj` together, read out the four settings that matter | 10 min |
| 4 | MIG wizard, page by page | 20 min |
| 5 | Clocking Wizard | 5 min |
| 6 | Add `top.sv` + `top.xdc`, synth/impl/bitstream | 15 min |
| 7 | Program, watch both LEDs | 5 min |

About an hour. If a page differs in his Vivado version, classify the setting
using the table above and the right value usually follows.
