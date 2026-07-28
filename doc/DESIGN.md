# DDR2-Backed INT8 Matrix-Vector Accelerator — Design Record

Nexys A7-50T (`xc7a50ticsg324-1L`), Vivado 2026.1.

This document records **why** each decision was made. What changed and when is in
the git log; the MIG configuration is in `mig_7series_0.xci`; the pin assignments
are in MIG's generated XDC. None of those record reasoning, so that is what lives
here.

---

## 1. The problem

Compute `y = A·x` where `A` is 1024×1024 signed INT8, `x` is 1024 signed INT8, and
`y` is 1024 signed INT32. `A` lives in DDR2. Host is Python over USB-UART. Results
must match NumPy exactly.

## 2. The governing insight

Matrix-vector multiply touches **each matrix byte exactly once**. Arithmetic
intensity is therefore **1 MAC per byte** — the lowest useful ratio there is.

That single fact determines the whole architecture: this design is **memory-bound
by construction**, not compute-bound. Matrix-*matrix* multiply reuses each byte N
times and would be compute-bound; GEMV never is.

Consequence: **lane count is set by memory bandwidth, not by DSP availability.**

## 3. Why DDR2 is genuinely required

| | |
|---|---|
| Matrix size | 1024 × 1024 × 1 B = **1 MiB** |
| XC7A50T block RAM | 75 × 36 Kb = **337.5 KiB** |

The matrix is **3.1× larger than all on-chip memory**. It physically cannot be
held in BRAM. This is not a contrived use of external memory — at this matrix
size there is no alternative.

## 4. Lane count = 16

The MIG user interface delivers **128 bits = 16 bytes per `ui_clk` cycle**. That
number is not a choice; it falls out of the hardware:

```
16-bit DDR2 bus  x  burst length 8  =  128 bits per user transaction
4:1 PHY ratio    ->  one transaction per ui_clk cycle
```

At an arithmetic intensity of 1 MAC/byte, consuming 16 bytes per cycle requires
exactly **16 MAC lanes**. A 17th lane would starve; only 16 bytes arrive.

**The XC7A50T has 120 DSP48E1. This design uses 16.** The other 104 sit idle on
purpose — adding lanes cannot help, because the bottleneck is the memory feed.
Being able to explain that is the point of the project.

## 5. Memory clock: 200 MHz, not 333 MHz

MIG *permitted* a 3000 ps clock period (333.33 MHz) for this part — the allowed
range it offered was 3000–5000 ps.

Digilent's own validated configuration, shipped as `mig.prj` inside the board
support files, uses **5000 ps (200 MHz)**. Digilent designed the PCB and know its
DDR2 routing quality; they deliberately left the tool's maximum on the table.

**Decision: match Digilent at 200 MHz.**

Reasoning: signal integrity on the board is not under our control, and a marginal
calibration fails intermittently and temperature-dependently — the most expensive
class of bug possible on a fixed deadline. 1.67× bandwidth is not worth that risk,
especially when the qualitative result (memory-bound, perfectly balanced) is
identical at either clock.

A copy of Digilent's file is kept at `doc/digilent_nexys_a7_mig.prj`.

## 6. Resulting numbers

| Quantity | Value | Derivation |
|---|---|---|
| Memory clock | 200 MHz | 5000 ps, vendor-validated |
| `ui_clk` | **50 MHz** | 200 MHz ÷ 4 (PHY ratio) |
| User-interface word | **128 bit = 16 B** | 16-bit bus × BL8 |
| Peak bandwidth | **800 MB/s** | 16 B × 50 MHz |
| MAC lanes | **16** | = 16 B/cycle × 1 MAC/B |
| Compute cycles | **65,536** | 1024 columns × 64 row-blocks |
| Compute time | **1.311 ms** | 65,536 ÷ 50 MHz |
| DDR2 stream time | **1.311 ms** | 1 MiB ÷ 800 MB/s |
| Throughput | **800 MMAC/s** (1.6 GOP/s) | |

Compute time and transfer time are **identical**, because both move 16 B/cycle.
With ping-pong tile buffers the DDR2 transfer is completely hidden behind compute.
The design sits exactly on its roofline balance point.

Side benefit: at 50 MHz, timing closure is not a concern.

## 7. Data layout — the host pre-tiles the matrix

Naive row-major storage would force the accelerator to gather 16 rows at one
column, i.e. 16 strided DDR2 reads per cycle. That destroys bandwidth.

Instead **Python rearranges the matrix before sending it**. Host-side shuffling
costs nothing; hardware-side shuffling costs logic and cycles.

For row-block `RB` (0..63) and column `j` (0..1023), 16 bytes are stored at:

```
byte_addr = RB*16384 + j*16
bytes[0..15] = A[RB*16+0][j] ... A[RB*16+15][j]
```

Bit `[7:0]` of the 128-bit word is the **lowest** row index; `[127:120]` is the
highest. This must match what NumPy packs, or results are silently wrong.

Consequences:

- each row-block is a **contiguous 16 KiB region** → one sequential DDR2 burst,
  no strided access
- one 128-bit read yields 16 different rows at the same column → feeds all 16
  lanes in one cycle
- `x[j]` is broadcast to every lane; lane *k* accumulates `y[RB*16+k]`
- **zero shuffle logic in RTL**

## 8. `app_addr` mapping

Read from the generated RTL (`ADDR_WIDTH = 27`, `ROW_WIDTH = 13`,
`BANK_WIDTH = 3`, `COL_WIDTH = 10`, `MEM_ADDR_ORDER = ROW_BANK_COLUMN`):

```
app_addr[26:0] = { rank[0], row[12:0], bank[2:0], col[9:0] }
```

The column address counts **16-bit words**, so one `app_addr` unit = 2 bytes.
Burst length 8 covers 8 columns = 16 bytes per transaction. Therefore:

> **Consecutive 128-bit transactions are 8 apart in `app_addr`** — not 1, not 16.

Check: 2²⁶ units × 2 B = 134,217,728 B = exactly 128 MiB, matching the chip.

Address formula for the tiled layout:

```
app_addr = byte_addr >> 1 = RB*8192 + j*8
```

The full matrix spans 524,288 units (2¹⁹), 0.8% of the address space.

## 9. Controller options

**ORDERING = Strict.** Normal mode lets the controller reorder commands to reduce
row activations. Our access pattern is purely sequential, so there is nothing to
reorder and Normal buys almost nothing. Strict guarantees **in-order read return**,
which reduces tile-fill logic to a plain counter — every `app_rd_data_valid` pulse
is the next word. Under Normal we would have to be certain the interface restores
request order; out-of-order returns would scramble tiles silently.

**Address map = ROW_BANK_COLUMN** (Digilent's file uses BANK_ROW_COLUMN). Crossing
a column block moves to the *next bank*, so the controller can activate the next
bank's row while still reading the current one, hiding the `tRC` penalty. This is a
performance knob only — it cannot affect correctness or calibration. Digilent's
choice is a demo default, not a tuned one. Revisit if measured bandwidth
disappoints.

**Native interface, not AXI4.** AXI4 would require writing an AXI *master* —
burst handshakes, ID tracking, response channels — for a streaming pattern that
needs none of it. The native interface is ~10 signals and is the one that actually
exposes DRAM behaviour: refresh stalls, `app_rdy` deasserting, bank conflicts.

**Internal Vref enabled.** Required — Digilent's configuration sets it. Legal here
because the data rate is 400 Mbps, inside the 800 Mbps limit for internal Vref.

## 10. Clocking

```
CLK100MHZ (pin E3)
    |
    v
clk_wiz_0  (MMCM, VCO 1000 MHz)
    +-- clk_out1 = 100 MHz (/10) --> mig sys_clk_i   ("No Buffer")
    +-- clk_out2 = 200 MHz (/5)  --> mig clk_ref_i   ("No Buffer")
    +-- locked                   --> sys_rst = locked & CPU_RESETN
              |
    MIG internal PLL: 100 MHz x 8 / 1 = 800 MHz VCO -> 200 MHz memory clock
                                                    -> ui_clk = 50 MHz
```

**Why a Clocking Wizard is unavoidable:** Artix-7's `IDELAYCTRL`, which calibrates
input delay taps during DDR2 training, requires a **200 MHz** reference. That is a
silicon requirement. The board provides only 100 MHz, so something must synthesise
200 MHz.

**Why both MIG clocks are "No Buffer":** the wizard owns the input buffer on pin
E3. Two input buffers cannot share one physical pin, so MIG must take its clocks
as internal nets.

**Why `sys_rst = locked & CPU_RESETN`:** MIG must stay in reset until the MMCM has
locked. Releasing it against an unlocked clock fails calibration in a way that
looks like a hardware fault.

Both wizard outputs use integer dividers off a single VCO, so there is no
frequency error.

## 11. Everything runs on `ui_clk`

UART, control FSM, MAC array and tile buffers are all clocked by MIG's `ui_clk`
(50 MHz). The only clock-domain crossing left in the design is inside MIG, which
Xilinx has already timed. **No CDC to design and none to debug.**

UART baud divisor at 50 MHz for 921600 baud: 50e6/921600 = 54.25 → 54, giving
925,926 baud, **+0.47% error**. Well inside the ~2% tolerance of 8N1 framing.

## 12. Bring-up order

MIG deliberately came first. It is the schedule risk, and testing calibration
needs nothing but an LED on `init_calib_complete` — so a failure surfaces early
with time to recover, rather than on the last afternoon.

| Stage | Proves | State |
|---|---|---|
| 1 | MIG generated and synthesised | **done** |
| 2 | `init_calib_complete` lights on hardware | in progress |
| 3 | UART echo round-trips all 256 byte values | |
| 4 | DDR2 write/read test driven from Python | |
| 5 | MAC array + tile buffer, simulated | |
| 6 | Full path, verified against NumPy | |
| 7 | Timing closure, measurements | |

Two LEDs during stage 2, because the failures differ: `LED[15] = mmcm_locked`
dark means a clocking problem; `LED[15]` lit with `LED[0]` dark means clocks are
fine and DDR2 calibration itself failed.

## 13. Measurements

To be filled in from hardware.

| Metric | Predicted | Measured |
|---|---|---|
| DDR2 read bandwidth | 800 MB/s | |
| Compute latency (1024×1024) | 1.311 ms | |
| Cycles stalled on DDR2 | ~0 (ping-pong) | |
| UART matrix load time | 11.4 s @ 921600 | |
| LUT / FF / DSP / BRAM | 16 DSP, ~10 BRAM36 | |
| WNS | | |

## 14. Known risks

- DDR2 calibration on hardware is unproven until stage 2 passes.
- UART is ~14,500× slower than compute (11.4 s load vs 0.786 ms of maths). The
  design is "load once, multiply many" by necessity. An LFSR fast-fill command is
  planned so regression runs do not pay the load cost.
- Byte ordering inside the 128-bit word must match between Python and RTL. A
  mismatch produces wrong answers with no error, so it is checked explicitly with
  a small case before the full matrix.
