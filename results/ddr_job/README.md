# Serial DDR job integration

The [latest pipeline regression](pipeline/README.md) tests the current row
and controller timing changes across all four P/T builds: 16 tests, 154 jobs,
103,086 checked outputs and 124 invalid descriptors. Its source and artifact
hashes are separate from the original regression and timing probe below.

## Original checkpoint

October 2, 2026: the production descriptor controller, tile DMA, AXI burst
engine, banks and compute engine passed the portable AXI RAM regression.
All 154 successful jobs are compared byte for byte: 82,106 useful INT32
outputs, input allocations, C row padding and surrounding guards.
The suite includes 100 seeded jobs and rejects 124 invalid descriptors.

| P | T | Tests | Completed jobs | Compared outputs | Macrotiles | Invalid descriptors |
| --- | --- | --- | --- | --- | --- | --- |
| 4 | 8 | 4 | 38 | 5,166 | 354 | 31 |
| 4 | 32 | 4 | 39 | 37,205 | 157 | 31 |
| 8 | 8 | 4 | 38 | 5,111 | 359 | 31 |
| 8 | 32 | 4 | 39 | 34,624 | 150 | 31 |
| Total | | 16 | 154 | 82,106 | 1,020 | 124 |

The independent oracle starts from A[M,K] and B[K,N], packs BT explicitly
and computes Python integer dot products. Directed cases cross P/T boundaries,
use non-square/odd shapes, K=255/256 and signed extrema. Every build includes
1024x1x256 and 1x1024x256 jobs, adjacent allocations and an allocation ending
exactly at the 128 MiB DDR boundary. Random padding remains nonzero.

Scoreboards check DMA order, actual AXI addresses/bursts, bank words, result
reads and every enabled W byte. Independent formulas check read/write volume
and compute cycles. The job timestamp is compared to the actual final B
handshake, including a test delaying the later DMA completion by 25 cycles.
START response backpressure does not pause accepted work or duplicate it.

Nine fault scenarios per build cover read/write response errors, core/local
result protocol faults, calibration loss in idle/validation/compute and
watchdog expiry with held AR or B. Fault snapshots stay frozen while issued
obligations drain; a hung path retains BUSY until recovery/reset. Invalid
descriptors issue no DMA and preserve the previous accepted job snapshot.

Reproduce with:

```text
make test-ddr-job PYTHON=.venv/bin/python
```

[`summary.json`](summary.json) records configurations, source fingerprints,
tool versions and artifact hashes. Per-build coverage and XML are copied
unchanged from that successful run. Its source maps identify the implementation
before the row-validation and controller timing changes. Running the command
above tests the current checkout; it does not recreate that historical source
identity unless the exact recorded inputs are restored.
The [controller contract](../../docs/ddr-job.md) defines counter and fault
sampling precisely. Register verification is [separate](../ddr_registers/README.md).

This is behavioral AXI RAM simulation. UART command/memory arbitration,
vendor integration, full-path routed timing, physical DDR GEMM, overlap and
four-read concurrency are not established by this result.

## Historical isolated controller timing probe

The earlier P4/T32 controller passed an isolated routed probe at 100 MHz with
0.2 ns uncertainty: setup +0.373 ns, hold +0.011 ns. It uses 1,710 LUTs,
2,040 FFs, six DSP48E1s, one BUFG and no BRAM. Three descriptor-size
multipliers use two DSPs each. These are measured controller resources;
they exclude the compute/DMA/platform and cannot establish full-board fit.

The worst setup path runs from the watchdog threshold through fault detection
into the shared public-counter snapshot enable. Delay is 9.072 ns:
3.252 ns logic and 5.820 ns routing. The six DSPs are not on that worst path.
There are no unconstrained internal endpoints, clockless registers, loops,
methodology findings or routing errors; all 3,257 routable nets complete.

All 513 external input paths and 666 external output paths are deliberately
excluded. This checks internal register timing with a routed BUFG, not the
controller-to-DMA/core interfaces or board I/O. The log retains an unexplained
`Constraints 18-8777` tile-splitting warning alongside OOC/optimization
warnings; this probe is not warning-free physical signoff.
See [the probe summary](timing_probe/summary.json),
[timing report](timing_probe/timing.txt), [utilization](timing_probe/utilization.txt),
[constraint checks](timing_probe/check_timing.txt) and
[warning record](timing_probe/warnings.txt).

To run the same isolated probe method on the current checkout from the
repository root in PowerShell:

```powershell
New-Item -ItemType Directory -Path build/ddr_job_probe -Force
Copy-Item rtl/control/gemm_ddr_job.sv build/ddr_job_probe/
Copy-Item results/ddr_job/timing_probe/gemm_ddr_job_probe.sv build/ddr_job_probe/
Copy-Item results/ddr_job/timing_probe/probe.tcl build/ddr_job_probe/
vivado -mode batch -notrace -source build/ddr_job_probe/probe.tcl
```

These commands copy the current controller, whose RTL has changed; their result
cannot be labeled a reproduction of the archived source fingerprint. Exact
historical reproduction requires controller SHA-256
`f88470f40ea87721694c58b0acd27e1ca78874b6e98c31683e12d37c9a336ac3`
under UTF-8/LF normalization. The as-tested wrapper and Tcl are archived with
their hashes. Generated checkpoints and full local consoles remain under
ignored `build/`.
