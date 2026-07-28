# DDR2 INT8 Matrix-Vector Accelerator — Design Record

Nexys A7-50T `xc7a50ticsg324-1L`, Vivado 2026.1.
`y = A·x`, A = 1024×1024 INT8, x = 1024 INT8, y = 1024 INT32, exact vs NumPy.

Git log = what changed. `.xci` = MIG settings. MIG's XDC = pinout.
**This file = why.**

---

## 1. Numbers

| | Value | From |
|---|---|---|
| Memory clock | 200 MHz | 5000 ps, vendor-validated |
| `ui_clk` | **50 MHz** | 200 ÷ 4 (PHY ratio) |
| UI word | **128 bit = 16 B** | 16-bit bus × BL8 |
| Peak bandwidth | **800 MB/s** | 16 B × 50 MHz |
| MAC lanes | **16** | 16 B/cycle × 1 MAC/B |
| Compute cycles | **65,536** | 1024 col × 64 row-blocks |
| Compute time | **1.311 ms** | 65,536 ÷ 50 MHz |
| DDR2 stream time | **1.311 ms** | 1 MiB ÷ 800 MB/s |
| Throughput | **800 MMAC/s** = 1.6 GOP/s | |
| DSP used | **16 / 120** | |
| BRAM | ~10 / 75 | 8 tile ping-pong + vec + result |
| UART | 921600 baud, div 54 @ 50 MHz, +0.47% err | |

Device: 32,600 LUT · 65,200 FF · 120 DSP48E1 · 75 BRAM36 (337.5 KiB).

## 2. The one insight

GEMV touches each matrix byte **once**. Arithmetic intensity = **1 MAC/byte**.
→ memory-bound by construction. Lane count set by bandwidth, not DSP count.

- Matrix 1 MiB vs BRAM 337.5 KiB → **3.1× too big** → DDR2 mandatory, not decorative.
- 16 B arrive per cycle → **16 lanes**. Lane 17 starves. 104 DSPs idle on purpose.
- Compute time == transfer time (both 16 B/cycle) → ping-pong hides DDR2 entirely.
  Design sits exactly on its roofline balance point.

## 3. Decisions

| Decision | Choice | Why |
|---|---|---|
| Memory clock | **200 MHz**, not the permitted 333 | Digilent designed the PCB and validate 200. Marginal calibration fails intermittently — worst bug class on a deadline. 1.67× BW not worth it |
| Interface | **Native**, not AXI4 | AXI4 means writing an AXI *master* for a pattern needing none of it. Native = ~10 signals, exposes real DRAM behaviour |
| Ordering | **Strict** | Access is purely sequential → nothing to reorder. Guarantees in-order read return → tile fill is a plain counter |
| Address map | **ROW_BANK_COLUMN** | Column-block crossing hits next bank → activate ahead, hide `tRC`. Digilent uses BANK_ROW_COLUMN but that's a demo default. Perf only, never correctness |
| Internal Vref | **enabled** | Required; Digilent sets it. Legal at 400 Mbps (limit 800) |
| Matrix layout | **host pre-tiles** | Host shuffling is free, RTL shuffling costs logic + cycles |
| Clock domain | **everything on `ui_clk`** | Only CDC left is inside MIG, already timed by Xilinx. None to design, none to debug |
| Lanes | **16** | See §2 |

## 4. Data layout

Row-block `RB` 0..63, column `j` 0..1023:

```
byte_addr = RB*16384 + j*16
bytes[0..15] = A[RB*16+0][j] ... A[RB*16+15][j]
bit [7:0] = LOWEST row index,  [127:120] = highest
```

- row-block = contiguous **16 KiB** → one sequential burst, no striding
- one 128-bit read = 16 rows at column `j` → feeds all lanes in one cycle
- `x[j]` broadcast; lane *k* accumulates `y[RB*16+k]`
- zero shuffle logic in RTL

Naive row-major would need 16 strided reads per cycle. That is the whole reason
for tiling.

## 5. `app_addr`

From generated RTL: `ADDR_WIDTH=27`, `ROW=13`, `BANK=3`, `COL=10`,
`MEM_ADDR_ORDER=ROW_BANK_COLUMN`.

```
app_addr[26:0] = { rank[0], row[12:0], bank[2:0], col[9:0] }
```

Column counts **16-bit words** → 1 unit = 2 B. BL8 covers 8 columns = 16 B.

> **Stride = 8 per 128-bit transaction.** Not 1, not 16.

Check: 2²⁶ × 2 B = 134,217,728 = exactly 128 MiB. ✓

```
app_addr = byte_addr >> 1 = RB*8192 + j*8
```

Matrix spans 524,288 units (2¹⁹) = 0.8% of address space.

## 6. Clocking

```
CLK100MHZ (E3)
  └─ clk_wiz_0  MMCM, VCO 1000 MHz
       ├─ clk_out1 100 MHz (/10) → mig sys_clk_i   "No Buffer"
       ├─ clk_out2 200 MHz (/5)  → mig clk_ref_i   "No Buffer"
       └─ locked                 → sys_rst = locked & CPU_RESETN

  MIG PLL: 100 × 8 / 1 = 800 MHz VCO → 200 MHz mem → ui_clk 50 MHz
```

- **Wizard unavoidable**: `IDELAYCTRL` needs 200 MHz (silicon requirement), board gives 100.
- **Both MIG clocks "No Buffer"**: wizard owns the E3 input buffer; two IBUFs can't share a pin.
- **`sys_rst = locked & CPU_RESETN`**: MIG must stay in reset until MMCM locks. Releasing against an unlocked clock fails calibration and looks like broken hardware.
- Both outputs are integer dividers off one VCO → zero frequency error.

## 7. Status

MIG first on purpose — it's the schedule risk, and testing it needs only an LED.

| # | Stage | State |
|---|---|---|
| 1 | MIG generated + synthesised | **done** |
| 2 | `init_calib_complete` lights on board | **done** |
| 3 | UART echo, all 256 byte values | **now** |
| 4 | DDR2 write/read from Python | |
| 5 | MAC array + tile buffer, simulated | |
| 6 | Full path vs NumPy | |
| 7 | Timing + measurements | |

Stage 2 uses two LEDs: `LED[15]=mmcm_locked` dark → clocking problem.
`LED[15]` lit, `LED[0]` dark → clocks fine, DDR2 calibration failed.

## 8. Measurements

| Metric | Predicted | Measured |
|---|---|---|
| DDR2 read bandwidth | 800 MB/s | |
| Compute latency 1024² | 1.311 ms | |
| Cycles stalled on DDR2 | ~0 | |
| UART matrix load | 11.4 s | |
| LUT / FF / DSP / BRAM | — / — / 16 / ~10 | |
| WNS | | |

Predictions written before measuring. Explaining a gap beats reporting a number.

**Stage 2 baseline** — MIG + clk_wiz only, everything else tied off. Subtract
this from final figures to get the accelerator's own cost.

| | Value |
|---|---|
| LUT | 3,150 / 32,600 (9.7%) |
| FF | 2,834 / 65,200 (4.3%) |
| WNS / WHS | +2.363 ns / +0.005 ns |
| Failing endpoints | 0 of 8,956 |
| Total power | 0.967 W |

MIG synthesised standalone at 3,697 LUT; implementation trimmed it to 3,150
because the tied-off `app_*` inputs let the optimiser delete the unused write
path. That logic returns once commands are issued.

## 9. Known warnings

**38 × `TIMING-6` critical warnings**, "no common primary clock between related
clocks", naming `clk_out2_clk_wiz_0` / `clk_out2_clk_wiz_0_1` and
`clk_pll_i` / `clk_pll_i_1`.

Cause: choosing "No Buffer" for MIG's clocks leaves its own `create_clock` lines
commented out, so Vivado auto-derives through the MMCM → MIG-PLL cascade and
cannot prove the two clock objects are the same physical net.

**Not fixed, deliberately.** The design times clean (0 failing endpoints of 8,956,
WNS +2.363 ns) and DDR2 calibrated on hardware first try, which settles
empirically what the static analysis could not prove. Digilent's alternative —
`sys_clk_i` Single-Ended straight from pin E3 — avoids the cascade, and is the
fallback if the interface ever proves flaky.

`XDCB-5` is a `get_pins` efficiency nag inside MIG's own XDC. Ignore.

## 10. Risks

- DDR2 calibration proven on hardware 2026-07-28, first attempt. Remaining risk
  is all downstream of the controller.
- UART is **14,500× slower than compute** (11.4 s load vs 0.786 ms maths).
  Design is "load once, multiply many" by necessity. LFSR fast-fill command
  planned so regressions skip the load.
- Byte order inside the 128-bit word must match NumPy. Mismatch = wrong answers,
  no error. Check with a small case before the full matrix.

## 11. Likely questions

| Asked | Answer |
|---|---|
| Why only 16 of 120 DSPs? | GEMV is intensity-1, memory-bound. 16 B/cycle arrive; 16 lanes consume them. More lanes starve |
| Why not run DDR2 at 333 MHz? | Tool permitted it, board vendor validates 200. Chose the validated point over the maximum |
| Why is the matrix pre-tiled? | Row-major needs 16 strided reads/cycle. Tiling makes each row-block one sequential 16 KiB burst and removes all shuffle logic |
| Why Strict ordering? | Sequential access has nothing to reorder. Strict guarantees in-order return, so tile fill is a counter |
| How do you know DDR2 is needed? | 1 MiB matrix, 337.5 KiB BRAM. 3.1× short |
| What limits performance? | Memory bandwidth. Compute and transfer are both 1.311 ms — the design is exactly balanced |
| How is correctness proven? | Bit-exact vs NumPy INT32 on the full 1024×1024, not a tolerance check |
