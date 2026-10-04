# Initial DDR GEMM timing failure

The first P4/T32 full-board implementation routed completely but failed the
100 MHz setup requirement: WNS -2.761 ns, TNS -3169.633 ns, with 5,936 failing
endpoints. Hold passed at +0.012 ns. This experiment generated no bitstream
and establishes no working DDR GEMM board result.

Vivado 2026.1 targeted `xc7a50ticsg324-1L`, with physical UART baud 115200.
The exact working source revision is recorded by input hashes in
[`summary.json`](summary.json); its build ID was `0xa969c4e5`.

## Measured cause

| Routed path | Slack | Finding |
| --- | ---: | --- |
| DMA row validation | -2.761 ns | Row-count multiplication, widened address addition and bounds checking occupied one cycle. |
| Host watchdog to memory control | -1.898 ns | Threshold subtraction and fault selection fed a 1,955-load admission signal. |
| Core reset through memory progress | -1.844 ns | A synchronous reset path reached the same host admission logic through handshake/progress decisions. |
| Job final-tile decision | -1.319 ns | Dimension comparison and completion/fault selection drove 512 snapshot enables. |
| Job watchdog threshold | -0.508 ns | Threshold subtraction remained on the fault-to-snapshot path. |

The selected path reports preserve actual routed endpoints and logic/route
delays. These results motivated staged row validation, precomputed watchdog
thresholds, registered tile-boundary flags and simpler idle admission logic.
They do not establish the outcome of those subsequent changes.

The routed design used 15,987 LUTs, 17,813 flip-flops, 24 DSPs and 10 RAMB36
equivalents (eight RAMB36 and four RAMB18). Capacity was sufficient; setup
timing was the failed gate. [`utilization.txt`](utilization.txt) contains the
final routed counts rather than synthesis estimates.

## Constraint and reset review

All 29,879 routable nets were routed without routing errors. There were zero
unconstrained internal endpoints and zero failed hold or pulse-width checks.
All ten generated SmartConnect bus-skew checks passed; minimum slack was
+8.945 ns. The CDC report contained no critical findings, with two vendor MIG
reset warnings and 353 SmartConnect/XPM enable-controlled crossing warnings.
Warnings remain recorded; this is not a warning-free implementation claim.

`CPU_RESETN` reached 43 MIG asynchronous preset pins and one MIG PLL reset,
as listed in [`reset_endpoints.txt`](reset_endpoints.txt). It reached no
application register data or enable endpoint. The failing internal core-reset
path above remained timed. UART input is excepted only to its first
synchronizer stage; asynchronous UART/LED output paths are explicitly
excluded. The generated MIG reset-through exception retained its original
Tcl expression in the exception report; this review did not independently
expand its resolved through-net. DDR warm-reset timing remains unqualified.

## Interrupted vendor simulation

The vendor simulation of the same source identity calibrated DDR and completed
the first 1x1x1 job, including signed `-128 * -128 = 16384`, in 200 job cycles.
It was interrupted after this routed failure was identified. There was no
final vendor PASS. The original marker lines and interruption record are saved
in [`vendor_excerpt.txt`](vendor_excerpt.txt) and
[`interrupted_vendor.json`](interrupted_vendor.json). The accelerated 10 Mbaud
simulation does not qualify that baud rate on hardware.

Every saved report is either a byte-identical complete report or an explicitly
described excerpt. `summary.json` records both original and saved byte hashes;
[`hashes.json`](hashes.json) covers every file in this evidence directory except
itself. Full consoles, checkpoints and reports remain under ignored `build/`.
