# AXI memory-path evidence

The 64-bit burst primitive passes independent transaction tests and synthesizes
for the Nexys A7-50T. It has one outstanding burst per direction and reserves
complete read/write buffers. The [interface contract](../../docs/axi-burst.md)
defines malformed responses, stalled VALID obligations and coordinated reset.
Separately, a generated AXI MIG passes its vendor DDR2-model smoke test. These
results do not yet connect the custom master to MIG or establish a DDR-backed
matrix job on the board.

All 43 recorded source hashes match commit
`4fabacfa0e370395af46396372d3af028f218db4`; see the [manifest](manifest.json).
Original execution checkout/dirty metadata remains in the vendor record.

## Portable verification

Four cocotb tests passed against `cocotbext-axi` RAM and a separate adversarial
response driver. The saved [coverage](burst/coverage.json),
[test results](burst/results.xml) and [source hashes](burst/summary.json) record:

- 18 RAM reads and 273 writes; every burst length from 1 through 16 and all
  256 byte-strobe masks, with untouched-byte guard checks.
- All 16 burst lengths ending exactly at a 4 KiB boundary, the
  final legal 32-bit-address beat, and 16 rejected descriptors.
- Independent address/data delays, both AW/W acceptance orders, stalled local
  read data and completions, and concurrent reads/writes.
- 21 fault scenarios, including response errors, wrong IDs, early/late/missing
  RLAST, premature responses and a fault while the other direction is active.
  A premature B cannot release a still-outstanding write.

Data seed: 271828. Channel-delay seeds: 9100 through 9104. Dependencies are
recorded in the summary and pinned in `requirements-test.txt`.

Run the focused test with `make test-axi PYTHON=.venv/bin/python`.

The complete `make lint test mutation PYTHON=.venv/bin/python` run also passed:
22 cocotb testcases, 20 host unit tests and three deliberately broken compute
implementations detected. The [regression record](regression/summary.json)
checks the source hashes and retains a compact result excerpt. These mutation
checks cover the compute core; they are not DMA or full-system mutation tests.

## Isolated synthesis

Vivado 2026.1, `xc7a50ticsg324-1L`, default `TAG_W=16`:

| Resource | Synthesized use |
| --- | ---: |
| LUTs | 332 |
| LUTs used as distributed RAM (included above) | 92 |
| Flip-flops | 150 |
| BRAM / DSP / latches / black boxes | 0 |

The [utilization report](synthesis/utilization.txt) and
[synthesis summary](synthesis/summary.json) describe this module alone.
Its 100 `Synth 8-3917` warnings report intentionally constant output bits;
there are zero errors or critical warnings. This run does not establish
routed timing or full-system resource use.

Reproduce the resource check in Vivado Tcl from the repository directory:

```tcl
read_verilog -sv {rtl/memory/gemm_axi_burst.sv}
synth_design -top gemm_axi_burst -part xc7a50ticsg324-1L -mode out_of_context -flatten_hierarchy rebuilt
report_utilization
```

## Vendor DDR2 simulation

The saved [MIG summary](vendor/summary.json) and
[warning/pass excerpt](vendor/console_excerpt.txt) come from the completed
`build/axi_mig_final` run, with Vivado 2026.1 and MIG 4.2 revision 2.

| Generated interface / observed traffic | Result |
| --- | --- |
| AXI data / address / ID widths | 128 / 27 / 4 bits |
| Configured DDR2 capacity / memory clock | 128 MiB / 200 MHz |
| Measured simulation user clock | 50 MHz |
| Preserved DDR pin assignments | 46 |
| Accepted AR / AW requests | 12 / 13 |
| Accepted R / W beats | 551 / 1,189 |
| Successful B responses | 13 |
| FAST calibration time / subsequent test interval | 163.594375 us / 50 us |

The vendor data checker passed, and the monitor checked response status,
clock period and retained calibration. Actual accepted AR/AW burst-start
addresses ranged from 0 to 14,380,400 bytes; this is not exhaustive memory
coverage. Narrow bursts are configured but were not exercised by this test.

There were no critical warnings. The excerpt retains vendor syntax/width
notices, the deprecated example-generation notice, the empty BoardPart
warning on reopening the part-based project, and model warnings about FAST
initialization and the absent XADC `design.txt` analog-temperature input.
Neither physical calibration timing nor temperature corners were tested.
The runner checked the exact FPGA, configuration reference and DDR pin map.

Reproduce this run with
`python scripts/test_mig.py --build-dir build/axi_mig_final`. A clean clone
can use `make sim-vendor PYTHON=python`, which defaults to `build/axi_mig`.
Vendor HDL/models are regenerated locally; only their hashes are saved here.
The width/clock bridge, physical AXI memory test and matrix DMA integration
remain to be implemented and validated.
