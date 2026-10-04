# Routed scalar fault-predicate revision

Build `0x36ffc81c`, P8/T32, four read slots, selectable serial/overlap mode.
Vivado 2026.1 routed the full system at the 100 MHz core constraint.

| Quantity | Result |
| --- | --- |
| Setup WNS / TNS | -0.237 ns / -2.171 ns |
| Failing setup endpoints | 14 |
| Hold margin | +0.020 ns; no hold failures |
| LUT / FF / DSP | 16,976 / 20,880 / 75 |
| BRAM equivalents | 20 RAMB36 |
| Occupied slices | 6,572 / 8,150 (80.64%) |
| Control sets | 1,513 |

The scalar fault predicate removes the previous error-code-to-scheduler
bottleneck. The remaining worst path starts at the four-read response-slot
FIFO, selects response metadata, detects a protocol fault and reaches host
progress/watchdog logic before resetting the host beat counter. Its 9.511 ns
data path contains 12 LUT levels; 7.567 ns is routing delay.

All 37,001 routable nets are complete without routing errors. Hold, pulse
and all ten SmartConnect bus-skew checks pass. Internal endpoints are
constrained; the full CDC report has no critical violations. The remaining
methodology warnings include control-set density and generated-memory
placement, as shown in the retained reports.

The timing gate exited with code 1 and prevented bitstream generation.
The [manifest](manifest.json) identifies the source and every retained report.
This is a failed qualification result, not a hardware performance claim.
