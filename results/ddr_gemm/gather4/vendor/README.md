# Four-cycle gathering: vendor integration

Vivado/XSim 2026.1 passes both complete UART-to-DDR GEMM jobs for build
`0x4898db67`, with P4/T32, serial scheduling and a 100 MHz core. All 65 framed
commands complete, and every one of the 16 output values matches the
independent integer reference.

| M x N x K | Outputs | JOB_CYCLES | COMPUTE_CYCLES | Read beats | Write beats / useful bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 x 1 x 1 | 1 | 204 | 12 | 2 | 1 / 4 |
| 5 x 3 x 9 | 15 | 850 | 40 | 16 | 10 / 60 |

Job counters exclude host traffic and end at the final successful write
response. Including host transfers, the fixture accepts 37 read beats,
48 write beats and 26 write responses. It uses actual UART pins, packet
handling, registers, the compute and DMA engines, SmartConnect, MIG and the
unmodified generated Micron DDR2 model.

Checks include signed extrema, odd/non-square outputs, nonzero input padding,
output padding through the next eight-byte boundary per row, two eight-byte
outer C guards, a host transfer crossing 4 KiB, upper-half write alignment,
invalid-descriptor rejection without DMA, and frozen counters during later
host traffic. Full allocation readback, maximum dimensions, deliberate
packet replay and endurance belong to separate tests.

The simulation uses 10 Mbaud UART, FAST calibration and ideal PCB
connections; the physical build setting remains 115200 baud. Retained
warnings cover generated-IP compilation, the unused XADC input file,
the empty board-part property and early CKE under FAST startup. There are no
simulation errors or additional DDR-model timing violations. This result
does not qualify physical UART timing, ISSI electrical margin or warm reset;
see the [memory limits](../../../../docs/ddr-memory-compatibility.md).

The [summary](summary.json), [input identity](inputs.json) and
[console excerpt](console_excerpt.txt) are exact copies or explicitly
identified excerpts of the completed run. All 28 source hashes matched at
completion. The recorded generated-file policy ignores only date comments
in three MIG constraint headers; original raw hashes remain recorded.
The base commit alone does not reproduce the original dirty working tree.
`manifest.json` binds every saved artifact by its byte hash.

Reproduce from the matching sources in a fresh directory:

```text
python scripts/build_ddr_gemm.py --stage sim --build-dir build/gemm_gather4_reproduce --sim-debug off
```

This is functional vendor-model evidence. Routed implementation and physical
matrix comparisons have separate gates; no new board speedup is claimed here.
