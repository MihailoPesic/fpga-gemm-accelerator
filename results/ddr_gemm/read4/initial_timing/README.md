# First four-read route

The first P8/T32, `READ_SLOTS=4` route missed the 100 MHz setup requirement:
WNS -0.470 ns, TNS -166.396 ns and 882 failing endpoints. Hold passed at
+0.013 ns. Routing completed, but the build gate generated no bitstream.
The FPGA retained its working one-read image.

Build `0xbb3e8247` passed its separate vendor simulation. Its frozen source
identity and report hashes are in [summary.json](summary.json) and
[manifest.json](manifest.json). Full reports and the checkpoint remain under
ignored `build/gemm_p8_read4_candidate/`.

The [worst path](host_response_path.txt) ran from the queued AXI response-slot
selection through protocol/progress/watchdog decisions into the host response
payload. Its ten logic levels took 10.047 ns: 1.689 ns logic and 8.358 ns
routing. The input arithmetic and systolic array were not on this path.

This result motivated using the existing first-error register for payload
capture and error publication. Immediate fault detection still controls memory
admission and draining. An error response takes one additional clock; a fault
on the completion decision edge must suppress success. The subsequent source
revision requires its own portable, vendor and physical qualification.

Saved timing/path excerpts retain original text. Utilization, route status,
constraint checks and pulse-violation reports are byte-identical full copies.
Their identities are sealed by the manifest; this failed experiment is not
counted as a passing implementation.
