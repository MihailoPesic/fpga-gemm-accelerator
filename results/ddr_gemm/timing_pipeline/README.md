# Pipelined DDR GEMM timing probe

The P4/T32 full-board design passes the 100 MHz routed timing checks in this
independent implementation: setup slack +0.081 ns and hold slack +0.010 ns.
These are small positive margins. All 29,824 routable nets were routed, with
zero routing errors and zero unconstrained internal endpoints. No bitstream
was generated, and this result does not qualify vendor simulation or hardware.

Vivado 2026.1 targeted `xc7a50ticsg324-1L` with UART baud 115200. Build ID
`0x2833cd6e` identifies the exact source/settings map in
[`summary.json`](summary.json). Simulation debug was configured off and its
baud parameter was 10 Mbaud, although this probe did not execute simulation.
All source, configuration and 362 generated-file identity checks passed.

This is now a historical fixture identity. The final corrected fixture
(`cec431bb...`) changes the full build ID to `0xed44f92e`; all 27 other original
inputs match this probe. Its reference-only XSim and Icarus checks each pass
194 cases and 776 bytes. This passed checkpoint remains valid for
`0x2833cd6e`; it does not qualify the corrected fixture's build. That build
requires its own successful vendor simulation and routed physical gates.
The failed reference run is preserved in
[`../vendor_oracle_failure`](../vendor_oracle_failure/README.md).

| Final routed resource | Count |
| --- | ---: |
| LUTs | 15,739 |
| Flip-flops | 17,953 |
| DSP48E1 | 24 |
| RAMB36 equivalents | 10: eight RAMB36 plus four RAMB18 |
| Occupied slices | 6,500 / 8,150 |

The failed initial implementation is retained in
[`../initial_timing`](../initial_timing/README.md). Staged row validation,
precomputed watchdog thresholds, registered tile-boundary flags and simpler
idle admission removed the previously identified failing paths.

## Remaining critical path

The worst setup path runs from the host memory-state register to the enable
of a response-payload register. Handshake and fault qualification determine
when a returned 64-bit word can be captured. Its data path is 9.542 ns:
1.572 ns of logic and 7.970 ns of routing, across nine LUT levels. The complete
first path is saved in [`setup_path.txt`](setup_path.txt).

The worst hold path is a direct packet-payload to saved write-payload register
transfer, with +0.010 ns slack; see [`hold_path.txt`](hold_path.txt). Neither
positive margin is a claim about a different placement, clock or P/T build.

## Constraint and reset review

Setup, hold and pulse checks pass. All ten generated SmartConnect bus-skew
checks pass. The CDC report has no critical findings; it retains two MIG
reset warnings and 353 SmartConnect/XPM enable-controlled crossing warnings.
The generated controller and bridge constraints were preserved.

`CPU_RESETN` reaches 43 MIG asynchronous preset pins and one MIG PLL reset,
with no application register data or enable endpoint. An independent query
of the copied routed checkpoint resolves the generated
`*/u_iodelay_ctrl/sys_rst_i` selector to exactly one net with those same
44 endpoints. The synthesis-stage empty-through warning therefore does not
describe the routed selector. See [`reset_selector.txt`](reset_selector.txt),
[`reset_query.tcl`](reset_query.tcl) and [`exceptions.txt`](exceptions.txt).

The asynchronous UART input exception ends at its first synchronizer stage.
UART output and four LED outputs have explicit asynchronous output exceptions.
Internal core reset paths remain timed. DDR warm-reset timing remains
unqualified; this static timing result does not establish memory recovery
after pressing reset or reprogramming a live DDR interface.

## Packing before P8

A read-only control-set report of this same routed checkpoint attributes
1,048 of the 1,658 sets to packet transport, compared with 28 in the tile
engine and three in the array. Vivado reports 1,357 additions from physical
replication. The transport's 1,968 surviving request-payload flip-flops combine
246 byte-write enables with 33 replicated reset nets into 977 control
combinations; 437 combinations drive only one flip-flop. This is a concrete
packing concern for future work on payload storage and reset handling.

Only 1,650 slices remain unoccupied. Unused LUT and register totals therefore
do not establish P8 headroom: 7-series registers sharing a slice must have
compatible control signals. [AMD's control-set guidance](https://docs.amd.com/r/2020.2-English/ug949-vivado-design-methodology/Control-Signals-and-Control-Sets)
explains this packing restriction. The isolated P8 tile result does not prove
that the full DDR system fits or meets timing. Replicated controls must not
be merged blindly, because placement used them to improve timing. Diagnostic
report hashes and per-hierarchy counts are recorded in `summary.json`.

## Warnings and evidence

The implementation retains noncritical DSP-pipeline, vendor reset/placement,
distributed-memory and control-set warnings. In particular, `ULMTCS-2` reports
1,658 control sets (20.34%), above the tool's 15% guideline. That is a real
packing and future scaling concern despite this configuration passing timing.
No claim of a warning-free design is made.

All 13 standard physical reports are saved in full, along with selected setup
and hold paths and the reset review. `generated_state.json` retains the full
generated-input fingerprints, including the narrowly documented MIG header
timestamp normalization and raw constraint hashes. `warnings.json` preserves
the run's warning messages. Original and saved report byte hashes are recorded
in `summary.json`; [`hashes.json`](hashes.json) covers every saved file except
itself. Full consoles and checkpoints remain in the ignored probe directory.

A releasable bitstream still requires the public builder's matching successful
vendor simulation and physical gates, followed by board output validation.
