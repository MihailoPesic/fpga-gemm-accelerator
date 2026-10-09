# Nexys GEMM

Signed INT8 matrix multiplication with INT32 results on the Nexys A7-50T. An 8x8
output-stationary systolic array uses banked BRAM, burst DMA and DDR2, with
overlapping load, compute and store. Python loads matrices and checks the
returned results over USB-UART. SmartConnect and MIG supply the vendor
width/clock conversion and DDR controller; the compute, DMA and control RTL
are in this repository.

At 100 MHz, a 256x256x256 job takes **2.97001 ms / 11.298 useful GOPS**
(median of 30 runs). This includes DDR tile loads, result writes and the final
write acknowledgement. Host packing, UART transfers and validation are timed
separately. [Measurements and method](docs/measurements.md).

The measured build is P8/T32, four read slots, 1 Mbaud, image `0x9d4beb4d`.
It supports serial and overlap modes on the same image. This is a development
release: board use requires a cold power-up; **DDR warm reset remains
unqualified**. [Current status and limits](docs/status.md).

## Design

```text
Control: Python -> USB-UART -> packets/registers -> scheduler

Load:    DDR2 -> MIG/SmartConnect -> read DMA -> A/BT banks
Compute: A/BT banks -> prefetch/skew -> 8x8 array -> C banks
Store:   C banks -> write DMA -> SmartConnect/MIG -> DDR2

DMA interface: 64-bit AXI, 100 MHz
MIG interface: 128-bit AXI, 50 MHz -> x16 DDR2, 200 MHz
```

The scheduler owns two operand sets and two result sets. It reserves output
space before launching a microtile, whose inputs are prefetched so DDR stalls
cannot interrupt compute. Results remain owned until their writes complete.
M and N can be 1..1024; K is 1..256. Input padding and partial PE rows/columns
are masked, and byte strobes preserve C row padding. See
[architecture](docs/architecture.md) and [module contracts](docs/README.md).

## Measured performance

P=8, signed INT8/INT32, 100 MHz, M=N=K=256; 30 samples per configuration.
Inputs remain in DDR between repetitions. T is the macrotile reuse size.

| Configuration | Median job cycles | Useful GOPS |
| --- | ---: | ---: |
| T8, serial | 1,698,969.5 | 1.975 |
| T32, serial | 741,063.5 | 4.528 |
| T32, overlap | 297,001 | 11.298 |

T8 to T32 reduces accepted input traffic by 4x and improves serial job time
by 2.293x. Overlap adds 2.495x, for a 5.720x ratio of cycle medians overall.
Larger tiles also reduce burst/control overhead; the runtime change is not
attributed to operand reuse alone.

![Measured useful GOPS by matrix size and reduction length](results/ddr_overlap/release_1mbaud/comparison/collector/useful_gops.png)

[Full comparison, traffic and tail latency](docs/measurements.md) includes
the raw counters, distributions, image identities and measurement limits.

## Verification

| Check | Evidence |
| --- | --- |
| Arithmetic and interfaces | Exhaustive INT8 operand pairs; independent integer oracle; tails, channel stalls, faults and deliberate defect detection. [Test map](docs/verification.md) |
| Board shape grid | 960 T32 jobs, 16 shapes, 30 samples per mode; every output and guarded allocation checked. [Record](results/ddr_overlap/release_1mbaud/board/t32/benchmark/README.md) |
| Maximum dimensions | 1024x1024x256 in both modes; 2,097,152 outputs independently replayed from retained bytes. One job per mode. [Record](results/ddr_overlap/release_1mbaud/board/t32/maximum/README.md) |
| Repeatability | 592 mixed jobs over 30.06 host-paced minutes; zero mismatches or UART retries. [Record](results/ddr_overlap/release_1mbaud/board/t32/endurance/README.md) |
| Implementation | 100 MHz routed setup/hold +0.017/+0.017 ns; 17,026 LUTs, 20,882 FFs, 75 DSPs, 20 RAMB36 equivalents. [Implementation and board evidence](results/ddr_overlap/release_1mbaud/README.md) |
| Scoped formal checks | FIFO induction and reduced scheduler ownership checks, with stated assumptions. [Proof scope](results/buffer_formal/README.md) |

Failed attempts and earlier images remain in the [history](docs/history.md).
Each record identifies its source, configuration and measurement scope.

## Try the portable core

Linux/WSL: Python 3.12, make and Icarus Verilog 12.0. No Vivado or vendor IP
downloads are needed for these tests.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/python scripts/test.py --only pe --build-dir build/core_smoke
```

Run the full regression with `make lint test mutation PYTHON=.venv/bin/python`.
[Testing](docs/testing.md) lists individual suites and optional formal checks.

## Build and use the FPGA

Use native Python and Vivado 2026.1 with Artix-7 support. From a clean clone:

```text
python -m pip install -r requirements-host.txt
python scripts/build_ddr_gemm.py --stage sim --overlap --p 8 --t 32 --read-slots 4 --baud 1000000 --build-dir build/gemm_current
python scripts/build_ddr_gemm.py --stage bitstream --overlap --p 8 --t 32 --read-slots 4 --baud 1000000 --build-dir build/gemm_current
```

After those stages pass, connect J6 USB, keep JP1 in JTAG mode, close serial
terminals and switch the board OFF then ON. Program this build and run:

```text
python scripts/program_ddr_gemm.py --manifest build/gemm_current/build.json
python -m host.gemm --port COM11 --manifest build/gemm_current/build.json --mode 1 --m 5 --n 3 --k 9 --repeats 3 --retries 0 --output build/my_gemm_demo
```

Replace COM11 with the board's port and choose a fresh output directory.
The demo compares every result and guards, prints cycles and saves JSON/CSV.
The [Python API](docs/host-gemm.md#use-your-own-matrices) accepts your own A/B.
[Board instructions](docs/ddr-board.md) cover qualification gates and the
shortcut for an already programmed image. A new build has its own identity;
the archived performance numbers apply to the recorded images.

## Repository

| Path | Contents |
| --- | --- |
| `rtl/core`, `rtl/memory`, `rtl/control` | Compute, banked buffers, DMA and control |
| `platform/nexys_a7` | Board wrapper, constraints and IP generation settings |
| `host/gemm`, `tb`, `formal`, `scripts` | Host API, tests, scoped proofs and build tools |
| `docs`, `results` | Contracts, decisions and retained measurements |

Generated projects, environments and bitstreams belong under ignored `build/`.
The root Vivado project is historical. Its `.srcs` tree still supplies UART
RTL and the MIG configuration used by the current scripts; preserve those
inputs. [Third-party material](THIRD_PARTY.md). The
[original v1 target](docs/specification.md) is separate from the implemented
[development contract](docs/ddr-registers.md).
