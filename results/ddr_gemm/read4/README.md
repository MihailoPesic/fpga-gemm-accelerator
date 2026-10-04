# Concurrent read DMA

The source build selects `READ_SLOTS=1` or `4`; the default remains one.
Four reads reserve both burst storage and ordered bank metadata before local
command acceptance. Load, compute and store remain serial per macrotile.

## Portable qualification

| Layer | Executed result |
| --- | --- |
| [Row planner](regression/rows/README.md) | 10 tests; 550 valid and 242 invalid requests; 9,257 accepted bursts |
| [AXI primitive](portable/axi/record.json) | 20 tests across both depths, including external cancellation |
| [Tile integration](portable/tile/record.json) | 56 tests; 216 GEMM jobs; 21,938 compared outputs; 128 invalid requests |
| [Packet/core with registered host geometry](host_geometry/core/record.json) | 80 tests across both depths and all four P/T builds; 64 GEMM jobs; 10,232 compared outputs |
| [P8/T32 job controller](portable/job/record.json) | 8 tests across both depths; 78 completed jobs; 87,946 compared outputs; 62 invalid descriptors |
| [Diagnostic compatibility](portable/diag/record.json) | 4 tests through the default one-read burst engine |
| [Build/program gates](gates/README.md) | 26 native unit tests; archived one-read P8 preflight passes |

The saved DMA snapshot includes the external fault-stop fix. The fresh
packet/core snapshot also includes registered host responses and burst geometry.
A held AR
is an AXI obligation and survives cancellation. Accepted local commands that
have never offered an AR retire in order with status 8 and no DDR effects.
Validated data and stalled terminals are immutable. Cancellation affects
read admission only and remains latched until coordinated reset. The enclosing
job preserves its original watchdog or calibration error and retains memory
ownership through every outstanding terminal.

The calibration-loss test starts with one held AR and three queued local
commands, verifies that only the held address reaches RAM, drains exactly
16 response beats, rejects competing host work and checks frozen counters and
unchanged memory. Tests use an independent wide-integer oracle and byte guards.
The AXI RAM fixture provisions four address entries so model capacity cannot
artificially limit the DUT to three accepted reads.
The [mutation checks](mutations/README.md) demonstrate that the permanent
regression detects missing external-stop handling and a one-edge-late stop.

## Controlled model comparison

The [profile summary](portable/profile/summary.json) and
[phase CSV](portable/profile/comparison.csv) compare identical P8/T32 matrices,
layouts, memory bytes and traffic with unstalled behavioral AXI RAM.

| Shape M/N/K | READ1 cycles | READ4 cycles | Model ratio | Cycles saved |
| --- | ---: | ---: | ---: | ---: |
| 32/32/256 | 12,343 | 9,795 | 1.260x | 2,548 |
| 31/29/17 | 4,044 | 3,626 | 1.115x | 418 |

These counts cover the software-driven A-load, BT-load, compute and C-store
helper sequence, including its request validation and completion observation.
They are not the full register JOB_CYCLES measurement. Compute and C-store
cycles match exactly; every saved cycle belongs to an input-load phase.
Both depths transfer the same bytes, addresses and strobes and compare every
output. The dense READ4 model reaches four accepted AXI reads; the short odd
case reaches two.

Run `make profile-read-dma PYTHON=.venv/bin/python` in a compatible simulator
environment. The runner requires a fresh output directory and records source,
tool and artifact identities. Generated simulator products stay under `build/`.

Each portable group's `record.json` binds exact copied summaries, coverage and
XML to its recorded source snapshot. No failed or skipped test is counted as a
pass. The earlier [tile snapshot](regression/tile/README.md) predates external
fault-stop propagation and retains its original identity.

The [vendor integration](vendor/README.md) passes two complete framed-UART
jobs through SmartConnect, MIG and the unchanged DDR2 model. Its
[first route](initial_timing/README.md) failed setup by 0.470 ns and generated
no bitstream. The [registered reply route](registered_reply/initial_timing/README.md)
reduced failing endpoints to 14 but still missed setup by 0.342 ns.
The [host-geometry revision](host_geometry/README.md) passes fresh portable
tests, both complete UART/MIG simulation jobs, the gated production bitstream
and copied-checkpoint reset review. Its [48-job physical suite](host_geometry/board/README.md)
also passes, with 49,593 outputs and all guards checked. Dense median latency
is 11,576.5 cycles, or 4.529 useful GOPS. This is the measured four-read
subsystem revision; the earlier model comparison retains its own scope. The
[physical revision comparison](board_comparison/README.md) preserves both
images' complete seven-case distributions and source differences. Dense median
job latency improves 1.83834x, with compute cycles and traffic unchanged.
