# Registered host fault response

The host-response timing revision passes 72 packet/core tests: nine tests
for each P4/P8, T8/T32 and READ1/READ4 configuration. These runs complete
64 GEMM jobs and compare all 10,232 outputs, with input and output guards.
[The core record](core/record.json) seals exact summaries, coverage, XML and
20 source/test inputs. No production test failed or was skipped.

The new cases remove DDR calibration during the first delivered read word and
at the read/write completion decision. Every case returns error 10 with an
empty payload, preserves memory ownership until draining finishes, retains
the first fault and holds backend/serial responses stable under backpressure.
Read/write data and guards remain checked. A private copy with the completion
fault-priority guard removed fails this permanent test; the production source
is unchanged by that experiment.

The [preceding route](../initial_timing/README.md) missed setup by 0.470 ns.
This revision uses the existing first-error register to cut the immediate
protocol/watchdog chain out of the wide response buffer. Error publication
takes one additional edge; memory cancellation/draining and watchdog detection
keep their immediate behavior.

The [route experiment](initial_timing/README.md) for `0x669f7769` reduced the
failing setup endpoints from 882 to 14, but still missed setup by 0.342 ns.
The remaining worst path calculates and validates host burst geometry in one
cycle. Its parallel vendor simulation was stopped after the first job;
there is no full vendor PASS or bitstream for this revision. These portable
results retain their original source identity and establish no new board speedup.
