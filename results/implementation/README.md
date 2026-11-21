# Vivado 2026.1 Routed Implementation Summary

Both `1 Mbaud` Nexys A7-50T (`xc7a50ticsg324-1L`) images share `P=8`, `100 MHz` core clock, four outstanding AXI reads, and selectable `MODE=0/1` scheduling. Post-route `phys_opt_design` (`AggressiveExplore`) closes timing without custom timing exceptions on custom RTL.

| Metric | `T32` Release (`0x9d4beb4d`) | `T8` Baseline (`0xeed7b111`) |
| --- | ---: | ---: |
| Core clock | `100 MHz` | `100 MHz` |
| Worst setup slack (WNS) | `+0.017 ns` | `+0.117 ns` |
| Worst hold slack (WHS) | `+0.017 ns` | `+0.015 ns` |
| Bus-skew checks / worst slack | `10` / `+9.019 ns` | `10` / `+8.996 ns` |
| LUTs (`32,600` avail.) | `17,026` (`52.23%`) | `16,956` (`52.01%`) |
| Flip-flops (`65,200` avail.) | `20,882` (`32.03%`) | `20,836` (`31.96%`) |
| DSP48E1 (`120` avail.) | `75` (`62.50%`) | `75` (`62.50%`) |
| RAMB36 / RAMB18 | `16` / `8` (`20` BRAM36 eq, `26.67%`) | `16` / `8` (`20` BRAM36 eq, `26.67%`) |
| Occupied slices (`8,150` avail.) | `7,001` (`85.90%`) | `6,787` (`83.28%`) |

## Key engineering observations

1. **Zero extra BRAMs for `4x` tile reuse:** Both `T8` (`8.5 KiB` logical banks) and `T32` (`40 KiB` logical banks) synthesize to the exact same `20` BRAM36 equivalents (`16 RAMB36 + 8 RAMB18`), because narrow `T8` banks underutilized Artix-7 BRAM primitive depth (Vivado emits eight `SYNTH-4` shallow-BRAM warnings on `T8`).
2. **Tight timing margin at high slice occupancy:** The initial overlap route missed `100 MHz` setup by `-2.029 ns` on fault-code selection feeding scheduler admission. Separating a registered scalar fault predicate reduced the miss to `-0.237 ns`, and post-route physical optimization closed `T32` at `+0.017 ns` across `69,902` timing endpoints (`12` logic levels, `9.785 ns` data path delay on `core/burst/queued_read.response_head_reg[0]` -> `core/overlap_path.job/error_code_reg[1]/CE`).
3. **Clocks and reset boundaries:** Core logic runs at `100 MHz`; AMD SmartConnect bridges `64-bit / 100 MHz` AXI to the `128-bit / 50 MHz` MIG interface (`x16 DDR2` at `200 MHz`). Board operation requires a cold power-up; warm-reset DDR clock/CKE timing remains unqualified.

## Reports

- `T32`: [t32_utilization.txt](t32_utilization.txt) | [t32_timing_summary.txt](t32_timing_summary.txt)
- `T8`: [t8_utilization.txt](t8_utilization.txt) | [t8_timing_summary.txt](t8_timing_summary.txt)
