# Nexys GEMM

Signed INT8 matrix multiplication with INT32 results for the Nexys A7-50T
(`xc7a50ticsg324-1L`). An output-stationary systolic array reuses operands from
banked local memory to compute `C = A * B`.

**Status: BRAM preview runs on the FPGA.** The 4x4 array accepts matrices over
USB-UART, computes in local memory and returns fully checked results. At
100 MHz, a 32x32x256 job takes 17,344 cycles (173.44 us), giving **3.02 useful
GOPS with inputs resident in BRAM**. UART transfers are measured separately.
DDR2 is not yet connected to GEMM.

## Implemented engine

```text
64-bit load -> A / transposed-B banks -> prefetch -> 4x4 or 8x8 array
                                                        |
                     tile control + cycle counters      v
64-bit read <--------------------------------------- C banks
```

Builds support P=4/8 and T=8/32, with 1 <= M,N <= T and 1 <= K <= 256.
The engine handles non-square shapes, masks tails, schedules microtiles and
holds read responses under backpressure. The [RTL entry point](rtl/control/gemm_tile_engine.sv)
and [interface contract](docs/tile-engine.md) describe the implemented design.

| Check | Saved result |
| --- | --- |
| Arithmetic and core | All 65,536 INT8 pairs; 500 seeded random microtiles per array size; three injected defects detected |
| Local engine | 164 matrix jobs, all 23,360 outputs checked across four P/T builds |
| Standalone core timing | P4/P8 pass at 100 MHz with 16/64 DSPs; excludes the banks and board wrapper |
| Integrated engine timing | Routed-clock harness: P4 setup/hold +0.948/+0.015 ns; P8 +0.422/+0.015 ns at 100 MHz |
| UART preview | Framing/retry, command-controller and serial-pin integration tests; host unit tests |
| Physical board | Six GEMM cases x 30 repetitions: 180 jobs, all 90,510 outputs checked; no mismatches or UART retries |
| Complete preview timing | 100 MHz: setup +0.188 ns, hold +0.005 ns; 9,166 LUTs, 6,387 registers, 16 DSPs, 10 RAMB36 equivalents |

The [preview interface](docs/preview.md) supports M,N up to 32 and K up to 256.
See the [verification, timing and board measurements](results/preview/README.md)
for the tested source/bitstream identity and measurement limits. The full
target adds DDR2 DMA, larger matrices and overlapping load/compute/store;
see the [specification](docs/specification.md).

## Run on the board

With Vivado 2026.1, Artix-7 support, Python and the Nexys A7-50T connected:

```sh
python -m pip install -r requirements-host.txt
python scripts/build_preview.py --baud 115200
python scripts/program_preview.py --manifest build/preview/build.json
python -m host.preview --port COM11 --manifest build/preview/build.json --repeats 30
```

Replace COM11 with the board's serial port. The build checks routed timing and
writes its manifest and bitstream under `build/preview/`. The demo checks
hardware identity and every output, then saves JSON/CSV under
`build/preview/hw_results/`. Repetitions reuse each case's uploaded inputs.
Use the [board workflow](docs/preview.md#build-and-inspect) to inspect the
routed design in Vivado.

## Run the tests

Requires Linux/WSL, Python 3.12, make, and Icarus Verilog 12.0. No Vivado or
vendor IP is needed for these tests.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
make lint test mutation PYTHON=.venv/bin/python
```

The tests compare RTL outputs against Python integer dot products. They cover
all signed INT8 operand pairs, seeded matrix jobs, tails, reset/restart,
cycle timing, and three injected defects. A GitHub Actions workflow runs the
portable checks. Tests also cover malformed UART frames, retries, command
errors, internal fault handling and full serial-pin matrix transactions.

## Design and evidence

- [Architecture](docs/architecture.md): current blocks and integration plan
- [Interfaces](docs/compute.md), [memory layout](docs/memory.md) and [design decisions](docs/decisions.md)
- [Build and test commands](docs/testing.md), [board configuration](platform/nexys_a7/README.md)
- Evidence: [board preview](results/preview/README.md), [compute](results/core/README.md), [operand memory](results/operand_memory/README.md), [local engine](results/tile_engine/README.md)
- [Current status and next deliverable](docs/status.md)

| Path | Purpose |
| --- | --- |
| `rtl/core/` | PE, systolic array, and microtile controller |
| `rtl/memory/` | Banked A/BT/C buffers and synchronous word prefetch |
| `rtl/control/` | Scheduling, counters, preview commands and framed UART transport |
| `tb/` | Automated verification and standalone timing harness |
| `scripts/`, `Makefile` | Simulation and Vivado build entry points |
| `platform/nexys_a7/` | Board/IP manifest and pin reference |
| `host/preview/`, `host/legacy/` | BRAM preview API/demo and retained native-DDR host tools |
| `docs/`, `results/` | Design contracts and measured evidence |
| `accelerator nexys.xpr`, `accelerator nexys.srcs/` | Existing UART/DDR2 board project and sources |
| `build/` | Ignored generated projects, logs, and local archives |
