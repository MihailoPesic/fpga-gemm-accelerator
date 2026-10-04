# Final routed overlap implementation

Build `0x2c680af7` completed the production bitstream stage with actual
pipeline and Vivado exit codes zero. P8/T32, four read commands outstanding,
selectable MODE=0/1, development version `0x200`, 100 MHz core and 115200-baud
physical UART. Routed setup/hold slack is +0.038/+0.016 ns;
all thirteen physical reports and ten SmartConnect bus-skew checks passed.

The implementation uses 17,030 LUTs, 20,874 flip-flops, 75 DSPs and 20 RAMB36
equivalents. Slice occupancy is 6,670/8,150 (81.84%). The array accounts for
64 DSPs; the remaining eleven implement DMA, validation and scheduler arithmetic.
The operand banks use sixteen RAMB36 blocks and the result banks eight RAMB18 blocks.

The worst setup path runs from queued read-response metadata through protocol,
progress and fault decisions to the job error-code register enable. Its twelve
LUT levels take 9.573 ns, including 7.629 ns of routing. The +0.038 ns setup margin
belongs to this exact routed image; it does not establish a higher operating clock.

The matching vendor simulation compared all 49 outputs across three UART jobs,
including a MODE=1 job spanning two macrotiles. Its accelerated calibration,
10-Mbaud simulation UART and ideal PCB delays do not qualify physical DDR timing.

[build.json](build.json) binds the final image, reports and vendor simulation.
[manual_review.json](manual_review.json) binds the clock/reset/CDC/exception and
resource review to this build's own checkpoint. The unchanged checkpoint review
record retains its literal `PASS_PROVISIONAL_REVIEW` label: it reports read-only
checkpoint analysis, and does not itself qualify a physical board. Every artifact
referenced by that record is retained under [review](review/record.json).

The bitstream and checkpoint byte hashes are recorded in [manifest.json](manifest.json);
those generated binaries are kept locally. Source/generated identities, exact Tcl
settings, actual execution receipts and original warning lines are preserved.
Vendor reset/clock advisories, bridge FIFO CDC warnings and DSP pipelining
advisories remain documented in the review. No warnings were waived. Some hard
PHY control pins have no modeled reset timing arc; static reports do not establish
their cold-start sequencing or electrical behavior.
This checkpoint establishes no physical-board measurements or warm-reset qualification.
