# Serial DDR GEMM routed implementation

The public build passed vendor simulation, routing and physical checks, then
generated the P4/T32 bitstream with build ID `0xed44f92e`. Vivado 2026.1 targets
`xc7a50ticsg324-1L`, with a 100 MHz core and physical UART baud 115200.
This record establishes implementation results; physical matrix validation
is recorded separately when completed.

The exact source/settings identity and generated-file hashes are in
[`summary.json`](summary.json). Bitstream SHA-256:

```text
fc2dcb3d43cf3f2775df5554de7f4a7e7612e9e4b41b6900a96afe2ce2bbc708
```

| Routed result | Value |
| --- | ---: |
| Setup slack | +0.097 ns |
| Hold slack | +0.014 ns |
| Pulse-width failing endpoints | 0 |
| Unconstrained internal endpoints | 0 |
| Fully routed nets / routing errors | 29,800 / 0 |
| LUTs / flip-flops | 15,778 / 17,940 |
| DSP48E1 | 24 |
| RAMB36 equivalents | 10: eight RAMB36 plus four RAMB18 |
| Occupied slices | 6,387 / 8,150 |
| Unique control sets | 1,393 (17.09%) |

Setup and hold margins are small positive results for this placement. They
do not establish a higher clock or another P/T configuration. Compared with
the historical route-only probe, occupied slices decreased from 6,500 and
control sets from 1,658. This is a placement result, not an architectural
speedup or proof that the full P8 system fits.

## Critical path

The worst setup path starts at `core/control/memory_address_reg[4]` and ends
at `core/burst/rd_done_status_reg[0]/CE`. Host-memory burst planning limits
the transfer at the next 4 KiB boundary; the selected length then feeds the
burst engine's command validation and completion-status enable. The path
contains 13 logic levels, including five CARRY4 stages. Data delay is
9.631 ns: 3.664 ns logic and 5.967 ns routing.

The worst hold path transfers packet-payload bit 265 into saved write-payload
bit 217, with no combinational logic and 0.284 ns data delay. Complete paths
and per-clock results are preserved in [`timing.txt`](timing.txt).

## Clocks, CDC and reset

The reports identify one 100 MHz primary input, the generated 100 MHz core,
200 MHz reference and 50 MHz MIG user clock, plus the vendor PHY clocks.
All ten SmartConnect bus-skew checks pass; minimum slack is +8.740 ns.
The pulse-violation report is empty. CDC has zero critical findings, eight
synchronizer information entries, two MIG reset warnings and 353 vendor
SmartConnect/XPM enable-controlled crossing warnings.

A separate read-only query copied this production checkpoint and checked its
hash before and after inspection. `CPU_RESETN` reaches exactly 43 MIG
asynchronous preset pins and one MIG PLL reset. There are no application
data or enable endpoints. The exact generated `*/u_iodelay_ctrl/sys_rst_i`
selector resolves to one net and the same 44 endpoints; its false path is
present in the routed exception report. This resolves the scope of the
synthesis-stage empty-through warning without adding an exception.
See [`reset_review.json`](reset_review.json),
[`reset_selector.txt`](reset_selector.txt) and
[`reset_exceptions.txt`](reset_exceptions.txt); the reproducible query is
[`reset_query.tcl`](reset_query.tcl).

The remaining asynchronous I/O entries are the UART input's first
synchronizer and the UART/LED outputs. Internal reset paths remain timed.
This review does not qualify DDR warm reset or physical memory margins;
see [memory compatibility](../../../docs/ddr-memory-compatibility.md).

## Retained warnings

The reports retain DSP-pipeline, vendor clock-buffer/reset placement,
distributed-memory and constraint-query warnings. `ULMTCS-2` reports control
sets above Vivado's 15% guideline, so packing remains a concrete concern for
future scaling. These warnings are retained alongside the passing timing,
CDC and routing gates; the design is not described as warning-free.

All 13 physical reports are saved in full. `summary.json` records their
original byte hashes, the bitstream identity and matching simulation record.
The read-only reset evidence has its own checkpoint/hash identity. Full
consoles and checkpoints remain under ignored `build/` directories.
