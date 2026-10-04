# Post-route physical optimization probe

Historical experiment on the routed checkpoint for build `0x36ffc81c`,
P8/T32 with four read slots. Vivado 2026.1 ran
`phys_opt_design -directive AggressiveExplore` after routing.

| Quantity | Result |
| --- | --- |
| Setup WNS / TNS | +0.043 ns / 0 ns |
| Hold margin | +0.020 ns; no failures |
| LUT / FF / DSP | 16,979 / 20,880 / 75 |
| BRAM equivalents / occupied slices | 20 RAMB36 / 6,572 (80.64%) |

The [original route](../initial_timing/README.md) missed setup by 0.237 ns.
Physical optimization changes placement and mapped logic while preserving
the RTL. All routing, pulse-width and ten bridge bus-skew checks pass;
internal endpoints are constrained and no critical physical or CDC findings
remain. The smallest setup margin is on the host memory-control path.

The [manifest](manifest.json) seals all 13 physical reports, the exact
[Tcl invocation](physopt.tcl) and [process exit](execution.json), plus source
and input/output checkpoint hashes. Checkpoint binaries remain build output.
The source files and original checkpoint/reports were unchanged during this
probe. This experiment generated no bitstream and ran no board test; it does
not qualify the later production build `0x6f0174fe`.
