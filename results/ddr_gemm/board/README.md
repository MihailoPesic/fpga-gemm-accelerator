# Serial DDR GEMM board qualification

The Nexys A7-50T passes 48 serial GEMM jobs over USB-UART, with all 49,593
outputs compared against a wide-integer oracle. Checks also cover 689,628
input, padding and allocation-guard bytes. There were no mismatches, UART
retries or rejected frames. The complete suite took 221.912 seconds.

This is the P4/T32 build at 100 MHz, with 115200-baud UART and build ID
`0xed44f92e`. JP1 was in JTAG mode; the operator confirmed a fresh OFF/ON
cycle with J6 connected before programming. The detected device was
`xc7a50t`. The reported DDR device is ISSI IS43DR16640C; its speed and
temperature suffix remain unreadable.

## Workloads and measurements

| M x N x K | Runs | Outputs checked | Job cycles min / median / max | Median useful GOPS |
| --- | ---: | ---: | --- | ---: |
| 1 x 1 x 1 | 3 | 3 | 205 / 206 / 206 | 0.000971 |
| 1 x 2 x 256 | 3 | 6 | 919 / 923 / 924 | 0.110943 |
| 5 x 3 x 9 | 3 | 45 | 848 / 852 / 865 | 0.031690 |
| 31 x 33 x 17 | 3 | 3,069 | 14,419 / 14,446 / 14,462 | 0.240773 |
| 33 x 35 x 256 | 3 | 3,465 | 55,601 / 55,610 / 55,653 | 1.063406 |
| 65 x 63 x 255 | 3 | 12,285 | 155,879 / 155,914 / 155,958 | 1.339488 |
| 32 x 32 x 256 | 30 | 30,720 | 34,577 / 34,637 / 34,673 | 1.513665 |

The scalar and two-column cases use signed extrema; the other matrices use
the seeds recorded in [manifest.json](manifest.json). Each case uploads its
inputs once and reuses those DDR contents for repetitions. These are seven
distinct matrices across 48 executions. Full input allocations, C row
padding and surrounding guards are read back and checked after every job.
Only the dense case has 30 repetitions.

For 32x32x256, median latency is 346.37 us. Useful throughput ranges from
1.512093 to 1.516291 GOPS. The calculation is:

```text
useful operations = 2 * M * N * K = 524288
useful GOPS = useful operations * core_hz / job_cycles / 1e9
```

`JOB_CYCLES` spans accepted START to the final successful result-write B
handshake. It includes DDR tile loads, local compute and result stores.
Inputs reside in DDR, and each job reloads its local banks. Packing, UART
movement, validation and guard checks are measured separately in the raw
records; 1.514 GOPS is not laptop-to-laptop throughput. Reused dense jobs
have median host-inclusive time 1.609742 seconds; the first dense run,
including upload, takes 4.735908 seconds. Those fields exclude validation
and guard checks, as specified in [summary.json](summary.json).

## Counter interpretation

Every dense job records 2,048 read beats, 512 write beats and 4,096 useful
write bytes: 16 KiB of operands plus 4 KiB of results. Its 64 microtiles
spend 17,088 cycles in active array schedules. That is about 49.3% of total
job time; useful MAC occupancy within those schedules is 95.9%.

The input-load counter has median 13,186 cycles. The remaining time includes
store, prefetch and controller work. Zero read-stall cycles means the core
never backpressured an offered R beat; it does not measure the delay before
data arrives. The 66 write-stall cycles per dense job also do not explain
the whole store phase. These counters establish a substantial cost outside
the active array, without proving physical DDR bandwidth saturation.

The next measurement should separate burst request/response latency, local
bank delivery, C-data collection and final write acknowledgement before
changing array size or concurrency. The earlier BRAM-local result measures
a different residency boundary and is not presented as a controlled speedup.

## Reproduce and inspect

Follow the [cold-start board procedure](../../../docs/ddr-board.md), then use
the manifest for the qualified, programmed image:

```text
python scripts/hw_test_ddr_gemm.py --port COMx --manifest build/gemm/build.json
```

[results.json](results.json), [results.csv](results.csv), [summary.json](summary.json)
and all seven case directories are exact copies of the completed run.
The [programming log](program.log) and
[cold-start record](cold_start_confirmation.json) bind the physical sequence
to the image. The [manifest](manifest.json) hashes all 19 raw artifacts.
The original dirty base commit is retained; the recorded 28 build-input and
six host/runner hashes identify the files actually used.

The [matching routed result](../routed/README.md) passes setup/hold at
+0.097/+0.014 ns, using 15,778 LUTs, 17,940 flip-flops, 24 DSPs and 10 BRAM36
equivalents. Its bitstream SHA-256 is:

```text
fc2dcb3d43cf3f2775df5554de7f4a7e7612e9e4b41b6900a96afe2ce2bbc708
```

Qualification is limited to this image, cold-start sequence and workloads.
It does not cover a 30-minute endurance run, the maximum 1024x1024 board
case, the full memory window, warm reset, P8, overlapping jobs or four-read
concurrency. [Memory compatibility](../../../docs/ddr-memory-compatibility.md)
records the configured Micron preset versus the reported ISSI chip and the
remaining electrical/identity limits. The full flagship contract remains
under development.
