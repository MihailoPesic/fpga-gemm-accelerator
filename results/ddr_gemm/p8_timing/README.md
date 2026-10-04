# P8/T32 full-system route probe

The complete serial DDR GEMM design fits the Nexys A7-50T and passes routed
100 MHz setup/hold in this isolated implementation: +0.256/+0.012 ns.
All ten SmartConnect bus-skew checks pass, with minimum slack +8.946 ns.
Routing is complete, with zero unconstrained internal endpoints and no
critical CDC, DRC or methodology findings. No bitstream was generated;
this result establishes neither vendor-simulation nor physical-board success.

Vivado 2026.1 targets `xc7a50ticsg324-1L`, P8/T32, serial scheduling and
115200-baud UART. Probe identity `0x01caf61c` uses the same 28 source files
as the revised P4 image, with P changed to eight. The original source commit
and dirty-tree flag are preserved in [the summary](summary.json); the source
hash map identifies the exact tested content.

| Resource | After synthesis | After routing |
| --- | ---: | ---: |
| LUTs | 17,249 | 16,751 |
| Flip-flops | 19,733 | 19,732 |
| DSP48E1 | 72 | 72 |
| RAMB36 equivalents | 20 | 20 |
| Control sets | 668 | 1,727 |

The routed RAM count is 16 RAMB36 plus eight RAMB18. Occupied slices are
6,623 of 8,150 (81.26%), and the 1,727 control sets represent 21.19% of the
slice count. Vivado retains its control-set warning. This leaves less packing
headroom than the unused LUT/flip-flop totals alone suggest; future buffering
and overlap logic still require their own implementation checks.

## Critical path and constraints

The worst setup path runs from `core/tile/k_q_reg[0]/C` to
`core/control/rsp_payload_reg[1870]/CE`. Its 9.493 ns data path contains
12 LUT levels: 1.944 ns logic and 7.549 ns routing. The synthesized path
connects tile readiness/progress to the host response-register enable;
it is not a PE multiplier path. The complete paths are saved in
[setup_paths.txt](setup_paths.txt) and [hold_paths.txt](hold_paths.txt).
These small positive margins apply to this placement at 100 MHz.

The routed `CPU_RESETN` fanout query finds exactly 43 MIG preset endpoints
and one MIG PLL reset, with no application data or enable endpoint; see
[reset_endpoints.txt](reset_endpoints.txt) and [exceptions.txt](exceptions.txt).
This probe did not perform a separate query of the generated reset-selector
net. The synthesis-stage empty-through warning remains visible in the saved
console. Internal reset paths remain timed, and UART/LED asynchronous I/O
exceptions retain their existing scope.

The CDC report retains two MIG reset warnings and 353 SmartConnect/XPM
enable-controlled crossing warnings. DSP pipeline, vendor placement/reset,
distributed-memory and control-set warnings also remain in the full reports.
No new timing exceptions or warning waivers were introduced. Warm reset and
the memory-device compatibility boundary remain separate qualification gates.

## Evidence and reproduction boundary

All 24 selected original reports/status files are copied byte-for-byte. The
console excerpts retain every warning/error, final marker and original line
number, together with the full local console byte hash. The archived
[attempt record](attempt_1.json), [input identity](probe_inputs.json),
configuration and probe Tcl retain their original bytes under `provenance/`;
their `.txt` suffix preserves raw line endings in Git. The private Python
launcher remains local, with its hash retained in the original input record. The
[manifest](manifest.json) records their source paths and byte hashes. The
routed checkpoint remains local, with its hash recorded in both summaries.

Preparation generated the IP and simulator scripts, but did not run XSim.
The attempt record separates preparation and route stages, including both
sets of generated-file hashes. All 362 canonical generated inputs remained
unchanged; only the documented MIG XDC header timestamp normalization is
allowed, with the original raw constraint hashes also retained. Reports in
this directory describe the subsequent routed design, not merely the
prepared project.

The private probe uses the archived P4 qualification as its launch gate and
omits `write_bitstream`. A releasable P8 image instead uses the public flow
in a fresh directory, which requires successful vendor simulation first:

```text
python scripts/build_ddr_gemm.py --stage sim --p 8 --t 32 --sim-debug off --build-dir build/gemm_p8_serial
python scripts/build_ddr_gemm.py --stage bitstream --p 8 --t 32 --sim-debug off --build-dir build/gemm_p8_serial
```

Those commands create separate qualification evidence. P8 vendor and board
qualification remain separate; overlap and read concurrency are not measured
by this probe.
