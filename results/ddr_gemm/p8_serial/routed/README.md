# P8 serial DDR GEMM: production build

The public builder completes the P8/T32 serial image for `xc7a50ticsg324-1L`
using Vivado 2026.1. Build `0x01caf61c` passes its matching
[vendor simulation](../vendor/README.md), every routed physical gate and
bitstream generation. The core clock is 100 MHz and physical UART setting
is 115200 baud. Physical-board correctness and performance remain unmeasured
for this image.

| Routed quantity | Result |
| --- | ---: |
| Setup slack | +0.256 ns |
| Hold slack | +0.012 ns |
| SmartConnect bus-skew checks | 10 passing; minimum +8.946 ns |
| LUTs | 16,751 / 32,600 |
| Flip-flops | 19,732 / 65,200 |
| DSP48E1 | 72 / 120 |
| RAMB36 equivalents | 20 / 75: 16 RAMB36 plus eight RAMB18 |
| Occupied slices | 6,623 / 8,150 (81.26%) |
| Control sets | 1,727 (21.19% of slice count) |
| Routed nets / routing errors | 34,926 / 0 |

There are zero unconstrained internal endpoints, critical CDC findings or
critical DRC/methodology violations. Pulse-width/period/skew checks pass.
The existing asynchronous UART/LED exceptions and generated platform
constraints were preserved; no new exception or warning waiver was added.

The worst setup path runs from `core/tile/k_q_reg[0]/C` to
`core/control/rsp_payload_reg[1870]/CE`, through load readiness, memory
progress and host response qualification. Its 12 LUT levels account for
1.944 ns of logic; routing contributes 7.549 ns, giving 9.493 ns data delay.
The worst hold path transfers a packet-payload bit into the command backend,
with 0.350 ns data delay and no intervening logic. Both paths are in the
full [timing report](timing.txt). Positive margins apply to this exact build,
not a higher clock or a future architecture change.

The control-set count remains above Vivado's guideline and restricts packing
headroom. Noncritical warnings also retain DSP pipelining, distributed-RAM
mapping and vendor reset/placement observations. The CDC report contains
eight informational crossings, two MIG reset warnings and 353 SmartConnect/
XPM enable-controlled crossing warnings. All reports and warning lines are
saved; this is not a warning-free implementation.

## Independent checkpoint review

A read-only query of a byte-identical copy of this production checkpoint
finds exactly 43 MIG preset pins and one MIG PLL reset reached by
`CPU_RESETN`. The generated `*/u_iodelay_ctrl/sys_rst_i` selector resolves
to one net with the same 44 endpoints, and its scoped false path is present
in the actual exception report. This addresses the synthesis-stage
empty-through warning without broadening the exception. See the
[reset query](review/reset_selector.txt), [exception report](review/exceptions.txt)
and [checkpoint identity](review/checkpoint_identity.json).

The separate [qualification audit](review/qualification_checks.json) checks
all 28 live source hashes, 362 generated-file identities, 13 physical report
hashes and the matching simulation/bitstream. The source and copied
checkpoints remained unchanged through the query.

## Evidence and reproduction

[summary.json](summary.json) is a byte-for-byte copy of the public build
manifest. It retains the original base commit and dirty-tree flag; the source
hash map identifies the actual tested files. Generated hashes allow only the
documented date-comment normalization in three MIG XDC headers and retain
the original raw constraint hashes. [manifest.json](manifest.json) binds
all 24 saved artifacts, including the complete physical reports and independent
review. The [console excerpt](console_excerpt.txt) preserves all warnings and
failure lines with original line numbers and the full local console hash.
Vendor RTL, generated projects, the checkpoint and bitstream remain local.

Reproduce with matching source content in a fresh directory:

```text
python scripts/build_ddr_gemm.py --stage sim --p 8 --t 32 --sim-debug off --build-dir build/gemm_p8_reproduce
python scripts/build_ddr_gemm.py --stage bitstream --p 8 --t 32 --sim-debug off --build-dir build/gemm_p8_reproduce
```

The earlier [route-only probe](../../p8_timing/README.md) has its own report
and checkpoint identities and generated no bitstream. This production build
adds the matching complete vendor simulation and bitstream gates. Neither
record establishes P8 board performance, warm-reset recovery, overlap or
concurrent-read behavior.
