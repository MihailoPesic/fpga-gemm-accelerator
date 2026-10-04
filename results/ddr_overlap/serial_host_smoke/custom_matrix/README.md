# Supplied-matrix API on the working FPGA

2026-10-04, COM11 at 115200 baud, existing serial image `0xd558a543`, P8/T32
at 100 MHz. The [documented API sequence](../../../../docs/host-gemm.md#use-your-own-matrices)
uploaded these ordinary mathematical matrices and read back every result:

```text
A = [[ 1, -2,  3],       B = [[ 7,   8],
     [ 4,  5, -6]]            [-9,  10],
                              [11, -12]]

C = [[ 58, -48],
     [-83, 154]]
```

All four signed INT32 results match. All 752 checked input, padding and guard
bytes are intact; there are zero retries or rejected frames. JOB_CYCLES is
261, including DDR transfers and final write acknowledgement. This small
functional check is not a dense throughput benchmark or overlap FPGA evidence.

[results.json](results.json) retains A/B/C, descriptor, hardware identity,
host source hashes, raw counters and the selected bitstream hash.
[record.json](record.json) seals that result artifact.
