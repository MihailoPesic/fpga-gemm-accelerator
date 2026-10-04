# Four-cycle C gather: routed implementation

The public build passed vendor simulation, routing and physical checks, then
generated the P4/T32 bitstream with build ID `0x4898db67`. Vivado 2026.1 targets
`xc7a50ticsg324-1L`, with a 100 MHz core and physical UART baud 115200.
These are implementation results. This image has not yet been measured on
the board; the earlier board results belong to `0xed44f92e`.

The exact source/settings identity and generated-file hashes are in
[`summary.json`](summary.json). Bitstream SHA-256:

```text
55eafc4dba5053b12d8299165b6d2deddb2dea9e7fb76d4ec6b4b4403cf1b910
```

| Routed result | Value |
| --- | ---: |
| Setup slack | +0.167 ns |
| Hold slack | +0.017 ns |
| Pulse-width failing endpoints | 0 |
| Unconstrained internal endpoints | 0 |
| Fully routed nets / routing errors | 29,808 / 0 |
| LUTs / flip-flops | 15,781 / 17,953 |
| DSP48E1 | 24 |
| RAMB36 equivalents | 10: eight RAMB36 plus four RAMB18 |
| Occupied slices | 6,498 / 8,150 (79.73%) |
| Unique control sets | 1,679 (20.60%) |

Setup and hold margins are small positive results for this placement. They
do not establish a higher clock or another P/T configuration. Occupied
slices and control sets increased from the earlier routed image's 6,387 and
1,393. Packing and placement changes cannot be inferred from LUT count
alone; this result does not establish that the full P8 system fits.

## Actual critical paths

The worst setup path starts at `core/control/memory_address_reg[4]` and ends
at `core/burst/read_state_reg[2]/D`. Host-memory burst planning limits the
transfer at the next 4 KiB boundary; the selected length then feeds the
burst engine's command validation and read-state update. There are 13 logic
levels, including four CARRY4 stages. Data delay is 9.695 ns: 3.506 ns logic
and 6.189 ns routing.

Other near-critical paths distribute tile state and packet index signals
to operand or packet registers. Those paths have only one LUT level but
about 9 ns of routing delay. Future scaling must account for this routing
and control-set pressure, as well as arithmetic resources.

The global worst hold path is inside MIG's read-leveling logic, from
`prev_sr_match_cyc2_r_reg[7]_inv` to `prev_sr_diff_r_reg[7]`, on the 50 MHz
`clk_pll_i` clock. Its data delay is 0.357 ns and slack is +0.017 ns.
Complete paths and per-clock results are preserved in
[`timing.txt`](timing.txt).

## Clocks, CDC and reset

The reports identify one 100 MHz primary input, the generated 100 MHz core,
200 MHz reference and 50 MHz MIG user clock, plus the vendor PHY clocks.
All ten SmartConnect bus-skew checks pass; minimum slack is +8.556 ns.
The pulse-violation report is empty. CDC has zero critical findings, eight
synchronizer information entries, two MIG reset warnings and 353 vendor
SmartConnect/XPM enable-controlled crossing warnings.

A separate read-only query copied this new production checkpoint and
verified the source and copy hashes before and after inspection.
`CPU_RESETN` reaches exactly 43 MIG asynchronous preset pins and one MIG PLL
reset, with no application data or enable endpoints. The exact generated
`*/u_iodelay_ctrl/sys_rst_i` selector resolves to one net and the same 44
endpoints; its false path is present in the routed exception report.
This confirms the scope of the synthesis-stage empty-through warning
without adding an exception.

See the exact [checkpoint identity](reset_review/checkpoint_identity.json),
[resolved endpoints](reset_review/reset_selector.txt),
[exceptions](reset_review/exceptions.txt) and
[query](reset_review/review.tcl). The proof binds this build manifest,
bitstream and checkpoint; its evidence hashes use paths relative to
`reset_review/`.

The two CDC reset warnings name the generated MIG reference-reset and
infrastructure-reset synchronizers. The remaining asynchronous I/O entries
are the UART input's first synchronizer and the UART/LED outputs. No broad
application timing exceptions were added. This review does not qualify
DDR warm reset or physical memory margins; see
[memory compatibility](../../../../docs/ddr-memory-compatibility.md).

## Retained warnings and provenance

DRC retains two `DPIP-1`, four `DPOP-1`, eight `DPOP-2` DSP-pipeline warnings
and one `REQP-1709` vendor clock-buffer warning. Methodology retains vendor
reset/placement, distributed-memory, unregistered BRAM-output and
constraint-query warnings. `ULMTCS-2` reports control sets above Vivado's
15% guideline. There are no critical CDC or DRC findings; the design is
not described as warning-free.

All 13 physical reports are saved in full. `summary.json` records their
original byte hashes, the bitstream identity and matching simulation record.
The [manifest](manifest.json) additionally hashes this explanation and the
read-only reset proof. Full consoles, generated IP and checkpoints remain
under ignored `build/` directories. The original build used a modified
working tree, so its commit alone does not reproduce these sources; the
28 recorded source hashes define the exact input boundary.
