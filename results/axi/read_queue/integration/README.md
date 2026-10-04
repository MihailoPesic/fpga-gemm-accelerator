# Default-slot integration compatibility

The updated `gemm_axi_burst` passes 48 existing integration tests using its
default `READ_SLOTS=1` implementation. The core and both test harnesses instantiate
that default explicitly through an unparameterized burst instance; this does
not enable four outstanding reads in GEMM.

| Boundary | Configurations | Tests | Checked work |
| --- | --- | ---: | --- |
| Framed DDR core | P4/P8, T8/T32 | 24 | 28 jobs, 5,056 outputs, 3,652 request/response pairs |
| Tile DMA, tile engine and AXI RAM | P4/P8, T8/T32 | 20 | 108 tile jobs, 11,039 outputs, 64 invalid requests |
| DDR diagnostic and AXI RAM | Default burst configuration | 4 | Three successful runs, 3,072 compared read words, 1,944 checked write words |

The core checks include replay/conflict handling, malformed memory requests,
backpressure and watchdog/calibration faults. Tile tests retain tails, guards,
both local buffer IDs and faults on the optimized C-gather transitions. The
diagnostic includes comparison faults at the final word, response errors,
stalled transactions and calibration loss. Raw AXI coverage counts include
host and fault-injection activity; they are not benchmark measurements.

[manifest.json](manifest.json) binds the complete original summaries, XML,
coverage and consoles to their tested source hashes. The summary versions are
preserved: core/tile runners report Python 3.12.3, while the simulator logs show
embedded Python 3.12.4. All simulations use Icarus Verilog 12.0 and cocotb 2.0.1.
The full logs retain Icarus sensitivity-list notices and library warnings.

The [compile console](lint/console.txt) also records a successful `scripts/lint.py`
run, including standalone one-slot/four-slot compilation. That run emitted no
independent input-hash manifest; the saved metadata distinguishes its current
script hash from the regressions' execution source seals.

These are portable behavioral-RAM checks. They establish no four-slot GEMM
integration, vendor-memory simulation, new routed timing, physical-board result
or formal proof. The separate [primitive tests](../README.md) cover the queued
read implementation. Earlier physical artifacts retain their original source
identities.

```text
python scripts/test_ddr_core.py --build-dir build/read_queue_core_reproduce
python scripts/test_tile_dma.py --build-dir build/read_queue_tile_reproduce
python scripts/test_ddr_diag.py --build-dir build/read_queue_diag_reproduce
python scripts/lint.py
```
