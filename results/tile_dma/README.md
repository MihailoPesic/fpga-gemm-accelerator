# Tile DMA integration

The [latest pipeline regression](pipeline/README.md) tests the current
registered row-validation RTL across all four P/T builds: 16 tests, 96 jobs
and 10,919 checked outputs. Its source and artifact hashes are separate from
the original checkpoint below.

## Original checkpoint

The serial tile DMA adapter passed the portable integration regression on
2026-10-02: 16 tests across four builds, 96 complete matrix jobs and 10,919
checked outputs. Each job loads A and BT through AXI into the production
banks, runs the real GEMM engine and stores C back to behavioral AXI RAM.
These saved artifacts precede the row-validation timing change. Their source
maps identify the historical implementation; they have not been rewritten.

| Build | Tests passed | Complete jobs | Compared outputs |
| --- | ---: | ---: | ---: |
| P4/T8 | 4 | 24 | 391 |
| P4/T32 | 4 | 24 | 5,898 |
| P8/T8 | 4 | 24 | 453 |
| P8/T32 | 4 | 24 | 4,177 |

The oracle generates B in its mathematical K-by-N layout, explicitly packs
BT and computes products using Python integers. It checks every useful C
byte and verifies untouched guards and row padding. Input padding contains
arbitrary nonzero bytes. Directed and seeded cases include non-square/odd
shapes, maximum local dimensions, K=1/7/8/9/255/256 and -128 extrema.
Both input and output buffer IDs are exercised.

The address scoreboard independently enumerates row-word addresses and
groups them by 4 KiB page and burst limit. It checks actual AR/AW transfers,
bank coordinates, result-pair reads and WSTRB values. Independent AXI and
local-port stalls check held payloads. All 64 invalid requests reject without
AXI or bank effects. Each build exercises 16 fault/delay bins, including
delayed/failing final B responses, malformed final read metadata, a wrong
odd-tail mask, pending bank requests through fatal events and actual write
collection cancellation in the burst engine. Failed stores never report success.

See the [summary](summary.json), [console excerpt](console_excerpt.txt) and
[compile check](compile_check.txt). The summary links unchanged per-build
coverage and XML, records all 15 tested input hashes, versions and deterministic
seeds, and retains raw/saved artifact hashes. Runner Python is 3.12.3;
the simulator embeds Python 3.12.4. Icarus is 12.0 with cocotb 2.0.1.
Existing library deprecation warnings and the packet compiler's constant-select
limitation do not change the recorded test verdicts.

```text
make test-tile-dma PYTHON=.venv/bin/python
```

This command tests the current checkout. It does not reproduce the historical
source identity unless those exact recorded inputs are restored.

The [contract](../../docs/tile-dma.md), [test source](../../tb/test_tile_dma.py)
and [runner](../../scripts/test_tile_dma.py) define this checkpoint.
M,N are local dimensions up to T and K is at most 256. The fixture excludes
MIG, UART descriptors, external macrotile iteration, overlap and four-read
concurrency. It has no new synthesis, routed timing, physical-board GEMM
or hardware-throughput result. Those require the complete DDR job controller
and board wrapper.
