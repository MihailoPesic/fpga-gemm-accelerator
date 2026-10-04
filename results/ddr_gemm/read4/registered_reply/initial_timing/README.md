# Registered response route

The P8/T32 READ4 route for `0x669f7769` still missed 100 MHz setup: WNS
-0.342 ns, TNS -2.955 ns and 14 failing endpoints. Hold passed at +0.009 ns.
The preceding route had 882 failing endpoints. Both attempts generated no
bitstream and changed no FPGA image.

The [worst path](host_burst_length_path.txt) now runs from host `words_left`
through burst planning and command validation into a read-slot state register.
It has 16 logic levels, including three carry stages, and takes 10.229 ns.
The earlier response-payload path is no longer the worst path. This result
motivated capturing host command geometry before presenting command VALID.

The complete route used 16,168 LUTs, 19,981 registers, 72 DSPs and 20 RAMB36
equivalents; 6,854 of 8,150 slices were occupied. These are failed-route
measurements, not estimates or a released implementation.

The parallel vendor simulation was deliberately stopped after this result.
It calibrated and completed the signed 1x1x1 job in 216 job cycles, but did
not complete the second job or produce a final PASS. The
[interruption record](interrupted_vendor.json) and
[marker excerpt](interrupted_vendor_excerpt.txt) preserve that limited result.

[Summary](summary.json) and [manifest](manifest.json) seal the source snapshot,
original reports and exact saved copies/excerpts. Full reports, checkpoints
and consoles remain under ignored `build/`.
