# First integrated overlap route

Build `0x2dd68757`, P8/T32, READ_SLOTS=4, 100 MHz, Vivado 2026.1.
The full design places and routes on the Nexys A7-50T, but setup timing fails.
The qualification gate exits with code 1 and produces no bitstream.

| Routed quantity | Result |
| --- | --- |
| Setup WNS / hold WHS | -2.029 / +0.014 ns |
| Failing setup endpoints | 5,869 |
| LUTs / flip-flops | 18,197 / 20,898 |
| DSPs / RAMB36 equivalents | 75 / 20 |
| Occupied slices / available | 7,008 / 8,150 (85.99%) |
| Unique control sets | 1,455 |

The worst path begins at the job shell's `error_code[0]` register and ends at
the frozen-counter clock enables. It traverses repeated diagnostic-code
normalization and priority selection through the scheduler and DMA, then drives
512 counter enables. Its data delay is 11.624 ns: 2.006 ns of logic and
9.618 ns of routing. Another path reaches a scheduler address update through
the same fault-code chain. This identifies the control dependency to remove.

The correction separates a scalar fault-presence signal from its 16-bit error
payload. Admission, stopping and snapshots use the scalar; error priority and
normalization remain in the diagnostic payload. No pipeline cycle is added.
Simulation assertions check that scalar detection and a nonzero selected code
agree. Fresh simulation and routing are required for that changed source.

The original route has no unconstrained internal endpoints or critical CDC
findings. All ten generated bridge bus-skew checks and pulse-width checks pass.
Generated-IP warnings and placement findings remain visible in the full
[methodology](methodology.txt), [CDC](cdc.txt) and [DRC](drc.txt) reports.
Passing those checks does not override the setup failure.

[manifest.json](manifest.json) preserves the exact failed input identity and
report hashes. [timing.txt](timing.txt) contains the complete timing paths;
[utilization.txt](utilization.txt) records physical packing. The corresponding
vendor run was stopped after this timing failure. Its partial logs remain in
the ignored local archive and provide no simulation qualification.
