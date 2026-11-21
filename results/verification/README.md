# Simulation, Mutation Testing, and Vendor MIG Summary

Portable RTL regressions run in CI ([`.github/workflows/core.yml`](../../.github/workflows/core.yml)) using Icarus Verilog 12.0 and `cocotb 2.0.1` across `P=4/8`, `T=8/32`, and `READ_SLOTS=1/4`.

## Core and interface coverage

- **Compute core (`gemm_pe`, `gemm_array`, `gemm_microtile`):** Exhaustive check of all `65,536` signed `INT8` operand pairs, overflow/sign extremes, clear-vs-in-flight product hazards, and `500` seeded microtiles per `P`.
- **Banked local memory and concurrent ports (`gemm_operand_banks`, `gemm_result_banks`, `gemm_tile_engine`):** Bank addressing, `1`-cycle BRAM read latency, tail masks, nonzero padding preservation, and disjoint concurrent load/compute/readback ownership.
- **AXI4 burst engine and 4-slot read queue (`gemm_axi_burst`):** `17` portable tests (`READ_SLOTS=1` and `4`), `354` queued read commands (`1,826` AXI `R` beats, all burst lengths `1..16`, occupancy `0..4`, 4 KiB splits, all `256` write-strobe masks, and protocol/error injection across every queue slot).
- **Integrated overlap subsystem (`gemm_dma_rows`, `gemm_tile_dma_duplex`, `gemm_tile_scheduler`, `gemm_ddr_overlap_job`, `gemm_ddr_core`):** `154` integrated tests and `1,056` complete simulated jobs (`800` seeded shell jobs), covering descriptor bounds, disjointness, watchdog, calibration loss, first-fault priority, and COBS/CRC UART packet handling.

## Injected RTL defect detection

Three permanent mutation suites inject single-line bugs into private RTL copies and require the designated regression test to fail ([mutations.json](mutations.json)):

| Suite | Runner | Target RTL | Mutants caught |
| --- | --- | --- | --- |
| Compute core | `scripts/mutation_test.py` | `gemm_pe.sv`, `gemm_microtile.sv` | `unsigned_multiply`, `product_survives_clear`, `early_drain` |
| AXI read-queue stop | `scripts/mutation_read_queue.py` | `gemm_axi_burst.sv` | `pending_reads_ignore_external_stop`, `external_stop_arrives_one_edge_late` |
| Concurrent buffer ownership | `scripts/mutation_tile_overlap.py` | `gemm_tile_engine.sv` | `response_owner_uses_live_request`, `active_result_reads_allowed`, `response_owner_released_early` |

## Vendor DDR2 simulation

- **Standalone AXI MIG smoke (`scripts/test_mig.py`):** `128 MiB` `x16 DDR2` (`200 MHz` memory clock, `50 MHz` UI clock, `128-bit` AXI, `46` preserved board pins), `FAST` calibration in `163.59 us`, `12` read bursts (`551` beats) and `13` write bursts (`1,189` beats) checked against the Micron/vendor DDR2 model.
- **Full-system vendor integration (`scripts/build_ddr_gemm.py --stage sim`):** Both `T32` and `T8` images passed three framed-UART jobs (`(1,1,1)`, `(5,3,9)`, `(1,9,9)`, `25` checked outputs, `94` packets, `66` read beats, `80` write beats, `40` write responses) through SmartConnect, MIG, and the DDR2 behavioral model (`10 Mbaud` sim UART, `FAST` calibration, ideal PCB delays).
