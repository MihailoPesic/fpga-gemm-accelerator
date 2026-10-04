# Maximum-shape board validation

One signed INT8 GEMM job with M=N=1024 and K=256 passed on the Nexys A7-50T:
all **1,048,576 INT32 outputs** matched the Python wide-integer oracle, and
**524,672 input/guard bytes** remained unchanged. The deterministic input seed was
`20261003`. UART recorded zero retries and zero rejected frames.

This is the previously programmed P4/T32 serial image `0xed44f92e`, at 100 MHz
and 115200 baud. Its bitstream SHA-256 is
`fc2dcb3d43cf3f2775df5554de7f4a7e7612e9e4b41b6900a96afe2ce2bbc708`. The as-built source state was dirty;
[inputs.json](inputs.json) preserves the 28 exact input hashes, while
[build.json](build.json) retains the original build identity and qualification.
This measurement does not qualify later RTL changes.

| Frozen job counter | Value |
| --- | ---: |
| JOB_CYCLES | 35,454,999 |
| COMPUTE_CYCLES | 17,498,112 |
| READ_BEATS | 2,097,152 |
| WRITE_BEATS | 524,288 |
| WRITE_VALID_BYTES | 4,194,304 |

The DDR-resident job took **0.354549990 seconds** and delivered
**1.514231920 useful GOPS**. JOB_CYCLES ends at the final successful
result-write B response; the counters exclude host memory transfers. Inputs were
uploaded for this single run. These are one-run measurements, not a latency distribution.

| Recorded host phase | Seconds |
| --- | ---: |
| packing_seconds | 0.133149100 |
| upload_seconds | 646.099347400 |
| configure_seconds | 0.192009100 |
| job_wall_seconds | 0.895957800 |
| download_seconds | 590.329787200 |
| validation_seconds | 25.810200300 |
| guard_read_check_seconds | 646.055116600 |

The sum of these measured phases is 1909.515567500 seconds. The CLI
did not record a separate total elapsed duration or finish timestamp.
`host_inclusive_seconds` is 1237.650250600; it excludes
validation and the extra guard read/check phase. Guard checking rereads complete
allocations, including useful C bytes that were already downloaded for validation.

The allocations and 64-byte guards cover byte addresses `[0, 4718976)`
(about 4.5 MiB), within the configured 128 MiB DDR window. There is no row
padding for this aligned maximum shape. This result is **not a full-memory
address sweep or a 30-minute repeatability test**.

The board continued the original powered/programmed session documented by
[the earlier board qualification](../board/README.md). The copied
[cold-start confirmation](cold_start_confirmation.json) and [program log](program.log)
are inherited records; no new cold cycle is claimed for this run. A new UART
connection performed the [read-only preflight](limits_preflight.json) before
loading inputs. Warm-reset qualification remains separate.

The exact recorded command was:

```text
python -m host.gemm --port COM11 --manifest build/gemm_reference/build.json --m 1024 --n 1024 --k 256 --repeats 1 --seed 20261003 --retries 0 --output build/gemm_reference/maximum_20261003_1052
```

Reproduction requires the matching qualified local manifest and bitstream, a
matching programmed board, the appropriate COM port, and a fresh output directory.
The generic host CLI checks bitstream/identity; archived simulation and routed
evidence were also checked using `scripts.program_ddr_gemm.qualified_manifest`.

[results.json](results.json) and [results.csv](results.csv) are exact raw copies.
[manifest.json](manifest.json) records their byte hashes, the inherited records,
host/source identities, and verification scope. Historical build fields such as
the original board-pending description are preserved rather than rewritten.
