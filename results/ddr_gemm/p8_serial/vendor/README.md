# P8 serial DDR GEMM: vendor integration

Vivado/XSim 2026.1 passes the complete UART-to-DDR test for P8/T32 build
`0x01caf61c`, with serial scheduling and a 100 MHz core. All 65 framed
commands complete, and all 16 output values match the independent integer
reference. The reference self-check passes before reset release.

| M x N x K | Outputs | JOB_CYCLES | COMPUTE_CYCLES | Read beats | Write beats / useful bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 x 1 x 1 | 1 | 216 | 24 | 2 | 1 / 4 |
| 5 x 3 x 9 | 15 | 838 | 32 | 16 | 10 / 60 |

Job counters exclude host traffic. `JOB_CYCLES` runs from validated START
acceptance to the final successful AXI B handshake, checked against observed
clock edges. Including host transfers, the complete fixture accepts 37 read
beats, 48 write beats and 26 write responses.

The fixture uses actual UART pins, packet handling, registers, compute/DMA,
SmartConnect, MIG and the unchanged generated Micron DDR2 model. It checks
signed extrema, nonzero input padding, every output byte, row padding through
the next eight-byte boundary and eight-byte guards around the C allocation.
It also checks a page-split host transfer, upper-half write alignment, odd
result strobes, invalid descriptors without DMA, and frozen counters during
later host traffic. Full allocation/input readback, maximum dimensions,
packet replay and endurance have separate tests.

Simulation uses 10 Mbaud UART, FAST calibration and ideal PCB connections;
the physical build setting is 115200 baud. All 20 warnings are retained in
[the console excerpt](console_excerpt.txt): generated-IP compilation,
the empty board-part property, missing optional XADC stimulus and early CKE
under FAST startup. No simulation error or additional command/data/refresh
timing violation was found. Physical UART timing, ISSI electrical margin and
warm reset remain outside this result; see the
[memory compatibility boundary](../../../../docs/ddr-memory-compatibility.md).

[summary.json](summary.json) and [inputs.json](inputs.json) are exact copies
of the successful run. All 28 source hashes matched at completion. The 362
generated inputs remained unchanged under the recorded policy, which ignores
only date comments in three MIG XDC headers and retains their raw hashes.
The original base commit and `source_dirty: true` are preserved; the base
commit alone does not identify the tested content. [manifest.json](manifest.json)
binds the saved artifacts to their byte hashes and the full local console.

Reproduce from matching sources in a fresh directory:

```text
python scripts/build_ddr_gemm.py --stage sim --p 8 --t 32 --sim-debug off --build-dir build/gemm_p8_reproduce
```

This is bounded functional vendor-model evidence. Routed implementation and
physical-board output/performance checks require their own results.
