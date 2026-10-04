# P8 maximum-shape board test

The Nexys A7-50T completed M=N=1024, K=256 in MODE=1 on image
`0x2c680af7`. All **1,048,576 INT32 outputs** matched the Python wide-integer
reference. The CLI also checked **524,672 input/padding/guard bytes**, with
zero UART retries or rejected frames. Seed: `20261004`.

| Frozen counter | Value |
| --- | ---: |
| JOB_CYCLES | 4,663,698 |
| COMPUTE_CYCLES | 4,571,136 |
| READ_BEATS | 2,097,152 |
| WRITE_BEATS | 524,288 |
| WRITE_VALID_BYTES | 4,194,304 |

At 100 MHz, this single DDR-resident job took **46.63698 ms** and delivered
**11.5117 useful GOPS**. The interval includes every tile load, result write
and final successful B response. It excludes host packing, USB-UART transfers
and validation. This is one maximum-size check, not a 30-sample distribution.

The complete test ran from 13:22:36 to 13:54:29 UTC on 4 October 2026.
Most elapsed time belongs to 115200-baud transfers. The existing CLI downloads
useful C, then rereads complete allocations for guard checks. Its
`host_inclusive_seconds` excludes validation and that extra read/check phase;
[results.json](results.json) preserves the separate timings.

This test continued the [previously qualified powered session](../board/README.md)
after fresh read-only identity/readiness checks. No new power cycle or warm-reset
qualification is claimed. The selected allocations cover about 4.5 MiB;
this is not a full 128 MiB memory sweep.

The actual wrapper and CLI exits were zero. [execution.json](execution.json),
[console.txt](console.txt), [results.csv](results.csv) and
[manifest.json](manifest.json) preserve execution, counters and exact hashes.
The original [build.json](build.json) retains its as-built commit and dirty
source state. Maximum output bytes were not saved by this CLI; the record
does not claim independent replay of those bytes.

Reproduce on the matching programmed image with a fresh output directory:

```sh
python -m host.gemm --port COM11 --manifest build/gemm_p8_overlap_final/build.json --mode 1 --m 1024 --n 1024 --k 256 --repeats 1 --seed 20261004 --retries 0 --output build/maximum_check
```
