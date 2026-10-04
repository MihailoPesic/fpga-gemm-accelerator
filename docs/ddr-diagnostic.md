# DDR diagnostic controller

`gemm_ddr_diag` is a destructive, bounded memory test for the 64-bit AXI
platform. It checks 4,096 selected bytes inside the 128 MiB window. It does
not run GEMM or sweep the whole DDR device.

The [October 2 board checkpoint](../results/ddr_platform/board/summary.json)
passes three seeded runs after one cold power-up. Its local image is
`build/ddr_diag_pipeline/gemm_ddr_diag.bit`, with the matching manifest at
`build/ddr_diag_pipeline/build.json` and BUILD_ID `0xc63e39aa`.

## Build and run

Run these commands from the repository root using Python and Vivado 2026.1.
The default device is `xc7a50ticsg324-1L`. Generate and simulate the complete
platform before implementing the same sources:

```text
python scripts/build_ddr_diag.py --stage sim
python scripts/build_ddr_diag.py --stage bitstream
```

Both stages use `build/ddr_diag`. Start with a fresh directory; the bitstream
stage must use the directory from the successful simulation. After complete
IP generation, the runner seals the project and generated sources with hashes.
An unchanged failed simulation can be retried in that directory. Preserve the
failure log before retrying. A source change, incomplete generation or a
modified project requires a new directory for both commands, for example
`--build-dir build/ddr_diag_retry1`. Use `--vivado PATH` if Vivado is installed
elsewhere. Do not modify the generated Vivado project between stages.

Generated-file hashes cover the file bytes, except the single date comment
in three known MIG 4.2 XDC headers that Vivado rewrites during implementation.
Their raw hashes are recorded separately; constraint commands, pin assignments,
other comments and line endings remain checked.

Simulation writes `simulation.json` after checking the custom burst engine,
SmartConnect, MIG and the generated DDR2 model. The fixture runs three seeds,
checks a single-beat read at byte address 8, the memory-window decode error,
and repeated jobs following initial calibration.
It initiates diagnostics internally; UART transport is tested separately.
The bitstream stage requires matching source and generated-platform hashes,
and writes `build.json` only after its implementation checks succeed. Inspect
the stage console logs and the timing, CDC, route, DRC and utilization reports
in the same directory. The [saved evidence](../results/ddr_platform/README.md)
distinguishes portable tests, vendor simulation, routed implementation and
physical-board qualification.

For the first hardware qualification, switch board power off, set programming
mode JP1 to JTAG using the board legend, and switch power on. JTAG mode prevents
another image from configuring the FPGA before this test; see section 2.1 of
the [Digilent manual](https://digilent.com/reference/_media/reference/programmable-logic/nexys-a7/nexys-a7_rm.pdf).
Connect through the J6 USB-UART/JTAG port. In Vivado,
open Hardware Manager, choose Open Target -> Auto Connect, select
`xc7a50t_0`, and use Program Device with the newly generated
`build/ddr_diag/gemm_ddr_diag.bit`. No debug-probes file is required.
The four LEDs are:

| LED | Meaning |
| --- | --- |
| 0 | DDR calibration ready |
| 1 | Diagnostic busy, including outstanding transfers draining after an error |
| 2 | Diagnostic completed successfully |
| 3 | Sticky error; coordinated platform reset required |

Programming alone does not start the memory test. Install the host dependency,
find the board's COM port in Device Manager, and run:

```text
python -m pip install -r requirements-host.txt
python -m host.ddr_diag --port COM11 --manifest build/ddr_diag/build.json --repeats 3 --seed 0x12345678 --output build/ddr_diag/hw_results
```

Replace `COM11` and the build paths as needed. Close other serial terminals
first. The CLI uses the manifest baud rate (115200 for this build), verifies
the local bitstream hash, and compares UART ID, version, BUILD_ID and clock
against the expected image. This checks image identity; it does not read back
and hash the programmed FPGA configuration. Each repeat uses a different
deterministic seed. The CLI exits nonzero on transport failure, hardware error,
timeout or unexpected completion counters, and saves `results.json` and
`results.csv`, including source identities, raw counters and host wall time.
It stops at the first failed run. Preserve that report before pressing the
board CPU RESET button or reprogramming; either resets the controller platform.
Warm-reset timing is not yet qualified, as described below.

## Reset qualification remains open

The exploratory warm-reset test recovered calibration and completed all data
comparisons, but the DDR2 model reported clock and CKE timing violations while
MIG's PLL was reset and restarted. That run is recorded as **FAIL** in
[the reset evidence](../results/ddr_platform/reset_probe/summary.json).
The model has no external reset pin; manually resetting its state would not
represent the board and is not used. The normal simulation gate covers initial
calibration, memory traffic and decode errors, not a timing-clean warm reset.

Run the separate qualification in its own build directory:

```text
python scripts/build_ddr_diag.py --stage sim --warm-reset --build-dir build/ddr_reset_qualification
```

This currently fails the model-error gate. Keep its logs; a functional recovery
message alone does not make it pass. No error suppression or generated-model
patch is applied. For the first physical test, record the power/configuration
sequence and start after board power-up without another image initializing DDR.
Reprogramming while DDR stays powered is also a warm-controller event. Every
reset invalidates existing memory contents and requires inputs to be reloaded.

## UART register interface

The diagnostic reuses the preview's COBS/CRC stop-and-wait framing and accepts
PING, READ_REG and WRITE_REG only; memory commands return BAD_CMD. Registers
have 16-bit byte addresses and aligned, complete 32-bit accesses. Each 64-bit
value has its low word at the listed address and high word at address + 4.

| Address | Register | Meaning |
| --- | --- | --- |
| `0x00` / `0x04` | ID / VERSION | `0x3144474e` (NGD1) / `0x00000100` |
| `0x0c` | STATUS | Bits 0..5: READY, BUSY, DONE, ERROR, DDR_READY, RESET_REQUIRED |
| `0x10` | CONTROL | Write exactly 1 to START; reads return zero |
| `0x14` | SEED | Idle-only write; reset default `0x12345678` |
| `0x40` | ERROR_CODE | First sticky diagnostic error |
| `0x48` / `0x50` | CORE_HZ / BUILD_ID | Build clock and source/configuration identity |
| `0x60` | FIRST_FAIL_ADDR | Byte address of the failing word or command |
| `0x68` / `0x70` | EXPECTED / ACTUAL | Frozen 64-bit mismatch words |
| `0x80` | CYCLES | Frozen 64-bit diagnostic duration |
| `0x90` / `0x98` | READ_BEATS / WRITE_BEATS | Frozen 64-bit local transfer counts |

READY requires calibration, idle state and no error. START acceptance clears
the visible previous DONE immediately, before the engine raises BUSY. A
second START or SEED write during that interval returns BUSY. There is no
CLEAR_STATUS or software abort command. Diagnostic-word reads return BUSY
during normal execution; after ERROR, the frozen record remains readable even
if outstanding memory traffic keeps BUSY high. START and SEED mutations in
fatal state return PROTOCOL without replacing the original ERROR_CODE.

## Data and address plan

Sixteen disjoint, 256-byte slots start at these byte addresses:

```
00000000 00001000 00010000 00080000
00100000 00200000 00400000 00800000
01000000 02000000 04000000 06000000
07000000 07800000 07f00000 07ffff00
```

For each 8-byte-aligned address `a` and the snapshotted 32-bit seed `s`:

```
low32  = rotate_left_32(a, 7) XOR NOT32(s) XOR a5c39e17
high32 = a XOR s XOR 3c6ef372
base_word = (high32 << 32) OR low32
overlay_word = NOT64(base_word)
```

Bytes use little-endian ordering. All slots are initialized before any read,
so aliases between different selected addresses can be detected. The phases
are:

1. Initialize every slot using two 16-beat writes per slot.
2. Read and compare every initialized byte, using two 16-beat reads per slot.
3. For slot `q=0..15`, write `q+1` beats starting at `slot_base+8`. Beat `k`
   uses strobe `masks[(q+k) modulo 8]`, where
   `masks = [0f, f0, 55, aa, 01, 80, 00, ff]`.
4. Read every complete slot again. Selected bytes must hold the overlay;
   all other bytes must retain their initialized values.

This exercises lengths 1..16, 8-modulo-16 starting addresses, partial/zero
strobes, guard words before and after overlays, and the highest legal word
at `07fffff8`. Every burst remains inside a 4 KiB page. Success requires
48 write commands, 64 read commands, 648 write beats and 1,024 read beats.

## Start, completion and errors

The controller uses one 100 MHz clock and synchronous active-high reset.
Its local command/data/completion ports connect directly to
[`gemm_axi_burst`](axi-burst.md), with 16-bit tags. It issues one command at
a time and waits for its terminal completion before preparing the next.

Each accepted read captures the received word, expected pattern and address.
The next cycle compares them while read data and completion are backpressured.
This separates pattern generation from comparison and fault distribution for
timing. A final completion cannot retire an unchecked last word. This bounded
diagnostic is not a peak-bandwidth test.

An idle successful `start` pulse snapshots `seed`, clears the previous DONE
and counters, and starts the test. A START while busy or in error is ignored;
the command frontend must report the corresponding rejection. If calibration
is lost between frontend acceptance and controller sampling, START latches
CALIB_LOST instead of silently disappearing. A completed successful test can
be repeated with a new seed.

DONE is sticky after success. ERROR and its first error record are sticky
until coordinated platform reset. Codes 7, 8, 9 and 10 mean memory response,
protocol, watchdog and calibration-loss errors; `0100` means data mismatch.
`first_fail_addr` identifies the first failing 64-bit word's byte address,
with the complete expected and actual words. For other faults it records the
current command address and leaves expected/actual zero.

The default no-progress watchdog is 10,000,000 core cycles. Accepted engine
or local transfers and internal command preparation reset its timer. A fault
prevents new commands from being prepared. An already offered command or
data item retains VALID and payload until accepted; an explicit rejected
write completion can cancel remaining local write data. Issued transactions
continue to drain. If the engine cannot complete an obligation, BUSY may
remain high until platform reset. Reset must include the controller, burst
engine, clock/width bridge and MIG.

## Counters and software observation

`cycles`, `read_beats` and `write_beats` start at zero and freeze on successful
completion or the first error, including the detecting edge. They stay frozen
while error draining continues, so software can safely read the complete
error snapshot even when ERROR and BUSY are both set.

Read beats count words accepted from the burst engine for checking; write beats
count words accepted into its local write buffer. Successful totals equal
the corresponding AXI beat counts. Fault snapshots exclude later draining
traffic and are not raw AXI bus counters or bandwidth measurements.

## Portable verification

`python scripts/test_ddr_diag.py` connects this controller to the production
burst engine and `cocotbext-axi` RAM. An independent byte-addressed oracle
checks actual AXI writes, read data, command addresses and untouched guards.
The suite covers three seeds, repeated runs, busy START and seed changes,
independent channel stalls, injected read/write response errors, a corrupted
read word, watchdog expiry with stalled AW, calibration loss with stalled AR,
and the START/calibration race. Simulation assertions check held local
payloads and reject unknown delivered read data. Vendor bridge, DDR model and
physical-board results are separate integration evidence.

The packet backend and host have separate focused regressions:

```text
python scripts/test_ddr_diag_control.py
python -m unittest discover -s tb -p test_host_ddr_diag.py
```

They cover command validation, response backpressure, duplicate-start
prevention before BUSY rises, frozen fault snapshots, host identity checks,
counter validation and JSON/CSV output. The host unit tests use a simulated
register device and do not access the board.
