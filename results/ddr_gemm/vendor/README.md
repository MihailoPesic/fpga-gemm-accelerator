# Serial DDR GEMM vendor simulation

Vivado/XSim 2026.1 completed the production UART-to-DDR path successfully:
65 framed commands, two GEMM jobs and all 16 output values checked. The
configuration is P4/T32, a 100 MHz core, serial scheduling and build ID
`0xed44f92e`. This is functional simulation evidence, not a board result.

| M x N x K | Outputs | JOB_CYCLES | COMPUTE_CYCLES | Read beats | Write beats / useful bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 x 1 x 1 | 1 | 206 | 12 | 2 | 1 / 4 |
| 5 x 3 x 9 | 15 | 860 | 40 | 16 | 10 / 60 |

Job counters exclude host memory traffic. `JOB_CYCLES` spans validated
START acceptance to the final successful AXI B handshake, independently
checked against observed clock edges. Including host transfers, the full
test accepts 37 read beats, 48 write beats and 26 write responses.

The fixture drives only UART pins and uses the real packet processor,
registers, DMA, compute engine, SmartConnect, MIG and unmodified DDR2 model.
It checks signed arithmetic, nonzero input padding, every output byte,
output padding through the next 8-byte boundary in each row, and 8-byte
guards before/after the C allocation. It also checks a host transfer across
4 KiB, upper-half write alignment, odd-output strobes, invalid-descriptor
rejection without DMA and frozen counters during later host traffic.
Full stride-padding/input-readback checks, deliberate packet replay,
maximum dimensions and endurance are outside this fixture's scope.

The reference self-check passes before FPGA reset release. Its independent
literal results and the [standalone reference regressions](../reference_tests/README.md)
cover the earlier [unknown-reference failure](../vendor_oracle_failure/README.md).
The old failed run remains recorded separately.

Simulation UART runs at 10 Mbaud to reduce runtime; the physical build
setting is 115200 baud. FAST calibration and ideal PCB connections do not
qualify physical UART timing, production startup, electrical margins or
warm reset. The full console contains the expected early-CKE warning from
FAST startup, generated-IP compilation warnings and a missing XADC input
file warning; no command/data/refresh violation or simulation error was
found. The signed-decimal BUILD_ID warning preserves its checked 32-bit
pattern. See the [memory compatibility limits](../../../docs/ddr-memory-compatibility.md).

Reproduce in a fresh build directory:

```text
python scripts/build_ddr_gemm.py --stage sim --build-dir build/gemm_reproduce
```

[summary.json](summary.json) is a byte-for-byte copy of the completed run's
summary, retaining base commit `35f8af4a7a04f6e8ef1d55f676fcef19455364cc`
and `source_dirty: true`. The 28 source hashes identify the actual tested
files; the base commit alone does not. All 28 hashes matched the checkout
at this audit. The 362 generated-file hashes also matched, using the
recorded policy that ignores only the date comments in three MIG XDC
headers. Their original raw hashes remain in the summary.

[manifest.json](manifest.json) binds the saved summary and
[console excerpt](console_excerpt.txt) to the complete local console hash.
Generated vendor code, the full console and project bookkeeping remain
under ignored `build/`. Routed timing, a qualified bitstream and physical
matrix comparisons are separate gates.
