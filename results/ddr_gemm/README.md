# Serial DDR GEMM board integration

The [board wrapper](../../platform/nexys_a7/gemm_ddr_top.sv) connects the
production UART PHY, packet subsystem and calibrated AXI DDR platform.
The [build flow](../../docs/ddr-board.md) binds source and generated-IP hashes
to the simulation, routed reports and bitstream identity.

## Build-flow checks

All 12 builder unit tests pass on Windows Python 3.13.0 and Linux/WSL
Python 3.12.3. Synthetic fixtures exercise rejection of stale sources,
changed generated files, incomplete simulation results and failing physical
reports. They also check manifest compatibility with the host library.
Changing the simulation debug setting invalidates the saved qualification.

The original summaries and consoles are saved in
[`build_tests/summary.json`](build_tests/summary.json),
[`build_tests/console.txt`](build_tests/console.txt),
[`build_tests/linux_summary.json`](build_tests/linux_summary.json) and
[`build_tests/linux_console.txt`](build_tests/linux_console.txt).
Their hashes match the tested inputs and original run artifacts.

```text
python -m unittest discover -s tb -p test_build_ddr_gemm.py -v
```

These checks do not run Vivado or access a board. Vendor simulation, full
routed timing and physical DDR GEMM require separate qualification results.

## Programmer checks

All 12 programmer tests pass on Windows and Linux, using synthetic evidence
and a mocked Vivado process. They reject modified/missing bitstreams,
simulation/configuration identities and routed reports before any launch.
Archived qualified images remain supported. Exact source and console hashes
are in [the Windows record](program_tests/windows_summary.json) and
[the Linux record](program_tests/linux_summary.json).

```text
python -m unittest discover -s tb -p test_program_ddr_gemm.py -v
```

## Initial integration result

The [first complete route](initial_timing/README.md) failed 100 MHz setup at
-2.761 ns. Its measured paths explain the registered row validation,
prepared watchdog thresholds and registered final-tile flags in the revised
RTL. The original reports retain the failed source identity; no bitstream
was generated from that experiment.

## Revised routed timing

The [registered-control route](timing_pipeline/README.md) passes all physical
checks at 100 MHz: setup +0.081 ns, hold +0.010 ns and all ten SmartConnect
bus-skew constraints. The complete design uses 15,739 LUTs, 17,953 flip-flops,
24 DSPs and 10 RAMB36 equivalents. The report records the narrow margins,
control-set warning and resolved reset exception.

This is a route-only result with its own sealed source/generated identities;
it generated no bitstream. The matching production build and physical
measurement have separate records below. Warm reset remains unqualified.

## Vendor reference failure

The revised vendor run passed the signed-extreme scalar job, then stopped
when its 5x3x9 comparison expected an unknown byte. The observed first C byte
matches independent integer arithmetic. The
[failure record](vendor_oracle_failure/README.md) retains the exact inputs,
console identity and fatal marker. It is a failed run with no bitstream or
physical-board qualification. The reference correction and fresh integration
are recorded separately below.

The corrected fixture passes the
[standalone reference regression](reference_tests/README.md) in native XSim
and Icarus: 194 arithmetic cases and 776 byte checks per simulator. Its early
16-result self-check also passes before DDR startup in the fresh integration
run.

## Complete vendor integration

The [fresh simulation](vendor/summary.json) passes both complete framed-UART
jobs through the production core, SmartConnect, MIG and unchanged DDR2 model.
Build `0xed44f92e` checks all 16 outputs: 1x1x1 in 206 core cycles and 5x3x9
in 860 cycles. The [console excerpt](vendor/console_excerpt.txt) retains all
warnings, progress, final traffic totals and normal completion.

This is functional simulation with FAST calibration, ideal PCB delays and
10 Mbaud UART; the physical image uses 115200 baud. Its matching production
route and board records follow.

## Production build and board result

The [production route](routed/README.md) passes 100 MHz setup/hold at
+0.097/+0.014 ns and all physical gates. The reset review uses the actual
new checkpoint, and the bitstream retains build ID `0xed44f92e`.

The [cold-start board qualification](board/README.md) passes seven matrices
across 48 jobs: all 49,593 outputs and 689,628 input/padding/guard bytes
checked, zero retries or rejected frames. The 30-run dense 32x32x256 case
takes median 34,637 cycles (346.37 us), or 1.514 useful GOPS including tile
loads and result writes. The raw records separate UART and validation time.
This establishes the original P4 serial checkpoint. P8 and P4 repeatability
results follow below; overlap, read concurrency and warm reset remain open.

The later [maximum-shape board check](maximum/README.md) passes one complete
1024x1024x256 job on the original image: all 1,048,576 outputs and 524,672
input/guard bytes checked, zero retries or rejected frames. It takes
35,454,999 cycles (354.54999 ms), or 1.514232 useful GOPS including DDR
transfers. This is a one-run boundary check in the continued powered session,
not a full-memory sweep or the repeatability workload.

## Cycle attribution and reuse

The [portable profile](profile/README.md) adds 16 jobs with all 25,156 outputs
checked, disjoint phase accounting and exact sensitivity to known per-burst
delays. At fixed P4/100 MHz and identical layouts/inputs, T32 reduces dense
input traffic from 65,536 to 16,384 bytes relative to T8. The trace identifies
five-clock C gathering and buffered read delivery. Compact job/burst tables
are saved; complete traces remain local. These behavioral-model results
apply to the original board-qualified sources and do not claim a new physical speedup.

## Four-cycle result gathering

The [paired profile comparison](gather4/profile/README.md) records the next
adapter revision. Successful collection skips the preparation state, reducing
the cadence from five to four cycles per 64-bit word. All 16 paired jobs save
exactly their write-beat count in JOB_CYCLES, with unchanged computation,
traffic, addresses, masks and other burst intervals. Dense T32 drops from
25,648 to 25,136 cycles in the unstalled AXI RAM model.

The [fresh portable regressions](gather4/regression/README.md) pass 60 tests
across all four P/T builds: 108 adapter jobs, 154 controller jobs and 28
packet-path jobs. Directed faults at the changed acceptance edges retain
stalled requests/data, zero-strobe drain and terminal-response obligations.

The prior vendor, routed and board records retain build `0xed44f92e`. The
revised image's [vendor simulation](gather4/vendor/README.md) now passes
65 packet exchanges and both jobs, with all 16 outputs checked. The jobs
take 204 and 850 core cycles. Its [production route](gather4/routed/README.md)
passes all physical gates at 100 MHz: setup +0.167 ns, hold +0.017 ns and
minimum bus-skew slack +8.556 ns. It uses 15,781 LUTs, 17,953 flip-flops,
24 DSPs and 10 RAMB36 equivalents. A separate query checks the actual new
checkpoint's reset endpoints and preserves the exact source/artifact hashes.

The [cold-start board test](gather4/board/README.md) now passes all 48 jobs on
bitstream `0x4898db67`: 49,593 outputs and 689,628 input/padding/guard bytes
checked, with zero retries or rejected frames. With the original workload,
clock, geometry and host sources, dense median latency falls from 34,637 to
34,082 cycles (340.82 us), a measured 1.60% reduction and 1.538 useful GOPS.
Compute and traffic counts match. The comparison preserves both physical
distributions; the exact 512-cycle isolated saving belongs to the behavioral
model. The maximum-shape record above still qualifies only the original image.

## P8 full-system fit

The [P8/T32 route-only probe](p8_timing/README.md) passes all routed checks
at 100 MHz: setup +0.256 ns, hold +0.012 ns and minimum bus-skew slack
+8.946 ns. It uses 16,751 LUTs, 19,732 flip-flops, 72 DSPs and 20 RAMB36
equivalents. All 28 source inputs and 362 canonical generated inputs remained
unchanged through the run. Full reports, source/configuration identity and
the actual reset-endpoint query are saved separately from the P4 results.

This establishes that the complete P8 serial design fits this device in one
successful placement. It generated no bitstream and does not qualify P8
vendor simulation, board execution or performance. The 81.26% slice occupancy
and 1,727 control sets remain relevant to later concurrency/overlap changes.

The subsequent [P8 public vendor integration](p8_serial/vendor/README.md)
passes both complete UART-to-DDR jobs under `0x01caf61c`: all 16 outputs
checked, 65 framed exchanges completed. The scalar and 5x3x9 jobs take
216/838 job cycles and 24/32 active compute cycles respectively. Its matching
production route/bitstream and physical measurements are recorded separately below.

The matching [P8 production image](p8_serial/routed/README.md) now passes
all routed gates and independent reset review on its new checkpoint, then
generates the bitstream. Setup/hold is +0.256/+0.012 ns at 100 MHz; all ten
bus-skew checks pass. It uses 16,751 LUTs, 19,732 flip-flops, 72 DSPs and 20
RAMB36 equivalents. The saved reset query resolves the generated selector
to the same 43 MIG preset pins and one PLL reset. Its physical qualification
has a separate record below.

## P4 repeatability

The [optimized P4 image](gather4/repeatability/README.md) completes nine full
suites over 1,999.298 seconds: 432 jobs, 446,337 output comparisons and
6,206,652 input/padding/guard-byte checks. No job fails and no transport
retries or rejected frames occur. All seven matrix pairs repeat across
passes, with complete output and allocation comparisons after every job.
This is host-paced repeatability at 115200 baud; actual summed DDR job time
is recorded separately from elapsed host/transfer/validation time.

## Array-scaling baseline

The [P4 baseline for P8 comparison](p8_scaling/p4/README.md) uses the updated
runner with explicit P4/P8 selection. All 48 jobs pass, with 49,593 outputs
and 689,628 input/padding/guard bytes checked. Its 30 dense runs take a median
34,103 cycles, or 1.53737 useful GOPS. Matrices, seeds and the P4 plan are
unchanged; host hashes are frozen for the subsequent P8 run. The earlier
optimization and repeatability records retain their original runner identity.

## P8 physical qualification and scaling

The [P8 board suite](p8_serial/board/README.md) passes all 48 jobs after fresh
cold power-up and programming of `0x01caf61c`: 49,593 outputs and 689,628
input/padding/guard bytes checked, with zero transport errors. Its dense
32x32x256 case measures 21,195 / 21,281.5 / 21,366 job cycles across 30 runs.
Median useful throughput is 2.46359 GOPS including DDR tile loads, result
writes and final response completion; host UART movement is separate.

The [P4/P8 comparison](p8_scaling/README.md) checks identical source code,
host code, matrix bytes, layouts, transfer counts and clock. Changing P4 to
P8 reduces dense median latency from 34,103 to 21,281.5 cycles: a measured
1.60247x speedup. All seven distributions are saved, including the scalar
case's small latency increase. P8 maximum-shape, 30-minute repeatability,
read concurrency, overlap and warm reset are not claimed by this result.
