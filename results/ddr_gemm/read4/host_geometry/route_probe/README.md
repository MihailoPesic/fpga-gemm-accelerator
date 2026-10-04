# Four-read timing experiment

The registered host-geometry source passes a complete P8/T32 route on the
Nexys A7-50T at 100 MHz. The native Vivado run exits zero with
`PASS_ROUTE_ONLY`; [summary.json](summary.json) binds the reports to build
`0xd558a543` and all 29 source/build inputs.

| Routed quantity | Result |
| --- | ---: |
| Setup / hold slack | +0.203 / +0.014 ns |
| Failing setup / hold endpoints | 0 / 0 |
| SmartConnect bus-skew checks | 10 passing; worst +9.058 ns |
| LUTs / registers | 16,133 / 19,960 |
| Occupied slices | 6,740 / 8,150 (82.70%) |
| DSPs / RAMB36 equivalents | 72 / 20 |

The [worst setup path](worst_setup_path.txt) runs from the queued read's
response-slot register to the host beat counter's reset input. Its nine logic
levels take 1.825 ns; routing takes 7.157 ns. This remains a narrow timing
margin and is not an estimate of maximum frequency. Registering host errors
and burst geometry cleared the earlier failing response/planning paths without
changing the clock constraint.

Slice occupancy is tighter than the LUT and register percentages suggest;
the 1,618 control sets also exceed Vivado's 15% guideline. Clock pairs have
expected related-clock or generated-IP exception classifications, with no
unconstrained internal endpoints. CDC has no critical findings; its enable
warnings are inside SmartConnect. The inherited MIG reset-selector warning
still requires a fresh endpoint query on the production checkpoint.

The experiment uses the production Tcl through every routing, CDC, setup,
hold, pulse-width, bus-skew and constraint gate, stopping before bitstream
generation. [Timing](timing_summary.txt), [utilization](utilization.txt),
[constraint checks](check_timing.txt), [route status](route.txt) and all
[warning/result lines](console_excerpt.txt) are preserved. The manifest seals
the copied evidence; full local reports and the routed DCP remain in ignored
`build/read4_geom_probe/` under their recorded hashes.

This run produces no bitstream and programs no hardware. Fresh vendor-model
simulation and the gated production build are separate qualification steps.
