# Vendor simulation: unknown expected byte

The P4/T32 UART-to-DDR simulation for build `0x2833cd6e` failed at simulated
time 2,565,692 ns. It calibrated and completed the 1x1x1 signed-extreme job
in 206 job cycles. During the following 5x3x9 job, the first C-byte comparison
reported:

```text
Fatal: C/guard mismatch job=2 address=00002fc0 expected=xx actual=c7
```

Independent Python integer arithmetic gives `C[0,0] = -16185 = 0xffffc0c7`,
so the observed first byte matches that calculation. The original fixture's
expected byte was unknown (`xx`). This isolates a problem with the comparison
reference at that point; it does not validate the rest of the matrix or prove
that the RTL is fault-free. A DUT-free reproducer now demonstrates the same
expected-side failure; see the isolated reference check below.

The run remains **FAIL_VENDOR_SIMULATION**. There is no final vendor PASS,
qualified bitstream, or DDR GEMM hardware result from it. The separately saved
routed timing pass does not override this simulation failure. UART simulation
used 10 Mbaud; the configured physical build was 115200 baud.

[`console_excerpt.txt`](console_excerpt.txt) preserves the original progress,
calibration, first-job and fatal lines. [`warnings_excerpt.txt`](warnings_excerpt.txt)
retains the vendor warnings, including the accelerated initialization warning.
[`reference_check.json`](reference_check.json) records the independent operands,
products and first-result calculation. The original `%0t` progress labels are
preserved verbatim; the vendor model printed their values in ps despite the
fixture's `time_ns` label.

[`summary.json`](summary.json) records all source hashes and the verified raw
hashes of the archived logs, generated metadata and source snapshot. The full
snapshot and generated files remain under ignored `build/`. Every public file
except the hash manifest itself is covered by [`hashes.json`](hashes.json).

## Isolated reference check

The extracted arithmetic reproduces the unknown value in XSim 2026.1 with
`--debug off --relax --mt auto`, without any DUT or DDR model. Every input
operand is known, but the nested expression returns `xxxxxxxx` for the second
job. Evaluating the operands and product in separate integer assignments fixes
that reproducer:

```text
a_term = a_value(job, row, k);
b_term = b_value(job, k, col);
product = a_term * b_term;
value = value + product;
```

| Reference form | XSim 2026.1 | Icarus |
| --- | --- | --- |
| Original nested expression | Unknown result; fails literal comparison | Pass |
| Explicit intermediates | Pass | Pass |

The passing probes check all 15 literal results of the second matrix and the
delayed task's byte comparison. Sources and original result-line excerpts are
saved as `oracle_baseline.*` and `oracle_intermediates.*`; each source is a
standalone alternative with module name `oracle_probe`. `summary.json` records
the full original compile/elaboration/run hashes. This demonstrates a
simulator-specific reference-evaluation failure and a tested workaround; it
does not claim a vendor-confirmed compiler defect. The original full vendor
run remains failed and must be rerun with the corrected fixture.
