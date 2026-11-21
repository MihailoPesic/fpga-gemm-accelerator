# Measured Results and Verification Summary

This directory contains the curated hardware measurements, Vivado implementation reports, mutation results, and formal proof summaries for the `1 Mbaud` Nexys A7-50T (`xc7a50ticsg324-1L`) release (`P=8`, signed `INT8 -> INT32`, `100 MHz`, four outstanding AXI reads).

| Area | Contents | Key result |
| --- | --- | --- |
| [Controlled tiling and overlap](comparison/README.md) | `comparison.csv` and PNG/PDF plots across 16 shapes (`1,440` board jobs) | `2.293x` (`T8 -> T32` serial), `2.495x` (overlap), `5.720x` total (`11.298 GOPS` on `256x256x256`) |
| [Implementation reports](implementation/README.md) | Vivado 2026.1 utilization and timing summaries for `T32` (`0x9d4beb4d`) and `T8` (`0xeed7b111`) | `+0.017 ns` setup/hold at `100 MHz`, `75` DSP48E1, `20` BRAM36 eq (`85.90%` slices) |
| [Physical board runs](board/README.md) | Dense `256x256x256`, `1024x1024x256` maximum, full 16-shape grid, CLI/API smoke, 30-minute endurance | `592` continuous mixed jobs (`30.06 min`), `2,097,152` max-size outputs, zero mismatches |
| [Simulation, mutations, and MIG](verification/README.md) | Consolidated [mutations.json](verification/mutations.json) and vendor DDR2 simulation summary | `8/8` injected RTL defects caught; Vivado `MIG 4.2` DDR2 simulation passed |
| [Scoped formal proofs](formal/README.md) | Pinned `Yosys 0.69` SAT proof summary and explicit assumptions | Inductive proof on 4-entry read FIFO; bounded SAT checks on scheduler and DMA row planner |

## Archived intermediate runs

Earlier development checkpoints (native DDR baseline, BRAM preview, `115200`-baud builds, raw VCD waveforms, full 20,000-line Vivado timing dumps, and per-job binary snapshots) are excluded from the main Git tree to keep the repository lightweight. Full raw archives can be attached separately as a GitHub Release asset (`v1.0-raw-evidence.tar.gz`) and all portable checks remain reproducible directly from source via `Makefile`.
