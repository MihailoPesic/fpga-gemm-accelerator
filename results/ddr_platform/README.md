# DDR platform verification

Portable tests and cold-start vendor simulation pass, including the updated
registered comparison. The first routed implementation failed setup timing;
the updated implementation passes 100 MHz timing and has a verified bitstream
manifest. Three physical DDR diagnostic runs pass after an operator-confirmed
cold power-up.
An exploratory vendor simulation also exposed a
warm-reset timing failure; both failures are retained below. The diagnostic
exercises selected memory locations, not GEMM or a full 128 MiB sweep. Its
interface and memory plan are in
[the diagnostic contract](../../docs/ddr-diagnostic.md).

## Portable regression

`make lint test mutation PYTHON=.venv/bin/python` completed with exit code 0 on
2026-10-02 using Icarus Verilog 12.0, cocotb 2.0.1 and cocotbext-axi 0.1.28.

| Check | Recorded result |
| --- | --- |
| Complete regression | 26 cocotb tests and 28 host unit tests passed |
| Diagnostic with production burst engine and behavioral AXI RAM | 3 tests; 3 successful complete runs; 3,072 read words compared; 1,944 write words checked |
| Diagnostic control interface | 1 test; 92 commands, 2 starts and 460 stalled-response cycles |
| Fault checks | Read/write response errors, data mismatch, watchdog, calibration loss and START/calibration race |
| Core mutation checks | All 3 inserted compute defects caught; no DMA mutation claim |

Each successful diagnostic initializes 4,096 selected bytes, performs masked
overlays, and checks all initialized locations twice. It completes 648 write
beats and 1,024 read beats. Tests also exercise repeated jobs, busy STARTs and
independently stalled AXI channels.

See [regression summary](portable/regression_summary.json),
[diagnostic coverage](portable/diagnostic/coverage.json),
[control coverage](portable/control/coverage.json) and
[mutation results](portable/core_mutations.json). The focused suites include
their original XML results. The console excerpt retains original line numbers;
its summary hashes the complete local console rather than presenting the
excerpt as a full log.

## Initial cold-start vendor simulation

`python scripts/build_ddr_diag.py --stage sim` passed in Vivado 2026.1 with the
custom burst engine, SmartConnect, MIG and generated DDR2 model. Three jobs
completed after initial calibration:

| Seed | Diagnostic cycles at 100 MHz | Read beats | Write beats |
| --- | ---: | ---: | ---: |
| `0x12345678` | 9,274 | 1,024 | 648 |
| `0xa5c319e7` | 9,201 | 1,024 | 648 |
| `0x0f1e2d3c` | 9,257 | 1,024 | 648 |

The fixture also checked an 8-byte read at address `0x08` and an error response
outside the DDR window at `0x08000000`. Totals were 3,074 read beats, 1,944 write
beats and 144 write responses. These are simulation results, not hardware
latency or bandwidth measurements. UART command execution is covered by the
portable tests; this fixture starts the diagnostic internally.

[The original summary](vendor/summary.json) retains all fields unchanged, with
line endings normalized to LF, including 16 source hashes, 362 generated-file
hashes and the dirty working-tree flag.
[The console excerpt](vendor/console_excerpt.txt) includes all 19 warning lines,
calibration and test markers, plus the complete console hash. It separately
records the raw original summary hash and the saved LF summary hash. No `ERROR` or
`CRITICAL WARNING` was reported. FAST calibration shortens initialization and
produces the model's 200 us CKE startup warning; other warnings concern generated
vendor HDL and the missing XADC analog stimulus file. The fixture uses ideal
board interconnect delays. It does not qualify physical DDR timing or warm
reset; `warm_reset_qualified` remains false.

## Warm-reset probe: failed qualification

The [probe summary](reset_probe/summary.json) and
[selected console lines](reset_probe/console_excerpt.txt) retain an abrupt
CPU_RESETN assertion after an injected address-decode error. Data comparisons
completed before and after recalibration, but the DDR2 model reported clock
timing and CKE-command violations during the reset/restart. This is a failed
warm-reset qualification despite the testbench's data-pass markers.

The captured three data runs account for 1,944 write beats and 144 write
responses. The 3,074 read beats include their 3,072 diagnostic words, one legal
single-beat read at address 0x08 and one decode-error response. Neither a manual
memory-model reset nor suppression of model errors is used to turn this run
into a pass. Cold power-on and warm reset remain distinct qualification cases.

## First routed implementation: setup timing failed

The 2026-10-02 diagnostic build reached full routing, but missed the 100 MHz
setup requirement. The build gate stopped before generating a bitstream.

| Quantity | Recorded result |
| --- | ---: |
| Worst setup slack | -0.727 ns |
| Total setup slack / failing endpoints | -86.022 ns / 240 |
| Worst hold slack / failing endpoints | +0.023 ns / 0 |
| Routing errors | 0 |
| LUTs / registers | 6,777 / 6,020 |
| RAMB36 equivalents / DSPs | 0 / 0 |

The worst path starts at `diagnostic/slot_reg[1]` and ends at the clock enable
of `diagnostic/seed_latched_reg[6]`. It crosses expected-data/address and fault
control logic: 19 logic levels and 10.409 ns of data-path delay, comprising
3.556 ns of logic and 6.853 ns of routing. This identifies a controller path
to shorten; it does not establish an achievable clock rate for the corrected
design. The array is not connected in this diagnostic build.

The [failure summary](timing_probe/summary.json) records the original input
identity, resource counts and report hashes. The [complete timing report](timing_probe/timing.txt),
[worst path excerpt](timing_probe/worst_path.txt), [utilization report](timing_probe/utilization.txt)
and [constraint checks](timing_probe/check_timing.txt) are preserved.
[DRC](timing_probe/drc.txt), [methodology](timing_probe/methodology_summary.txt)
and [CDC](timing_probe/cdc_summary.txt) findings remain visible; their warning
summaries do not constitute signoff. In particular, `CPU_RESETN` has no input
delay and still needs its asynchronous-reset constraint review. The large CDC
detail report and routed checkpoint remain local, with hashes in the summary.
The subsequent read-only [bus-skew check](timing_probe/bus_skew_summary.txt)
on that same checkpoint passed all 10 generated constraints, with worst slack
+9.008 ns. That result does not change the failed setup-timing verdict.

## Registered comparison: focused regression

The diagnostic now registers each returned word, its expected value and its
address before comparing them. A pending comparison blocks the next data or
completion handshake, so the final word must pass before another command or
DONE can be accepted. This separates expected-data generation from the fault
decision that appeared in the failed timing path.

The [focused regression](pipeline_check/summary.json) passed all four tests
with the production burst engine and behavioral AXI RAM; lint also passed.
Alongside the existing successful runs and fault checks, it corrupts the final
word of the first burst at `0x00000078` and the final word of the job at
`0x07fffff8`. Both cases check the exact failed address, expected/actual data,
and absence of a later command or successful DONE. The
[coverage record](pipeline_check/coverage.json) preserves these cases.

The updated vendor simulation and routed implementation also passed, as
recorded below. The failed timing reports above describe the earlier input
hashes. Physical-board results are recorded separately below.

## Registered comparison: vendor simulation

`python scripts/build_ddr_diag.py --stage sim --build-dir build/ddr_diag_pipeline`
completed with exit code 0 in Vivado 2026.1. Build ID `0xc63e39aa` uses the
registered comparison with the production burst engine, SmartConnect, MIG
and generated DDR2 model. Three jobs passed after cold-start calibration:

| Seed | Diagnostic cycles at 100 MHz | Read beats | Write beats |
| --- | ---: | ---: | ---: |
| `0x12345678` | 10,468 | 1,024 | 648 |
| `0xa5c319e7` | 10,605 | 1,024 | 648 |
| `0x0f1e2d3c` | 10,595 | 1,024 | 648 |

The fixture again checked the single 8-byte read at `0x08` and the decode
error at `0x08000000`. Totals were 3,074 read beats, 1,944 write beats and 144
write responses. The extra comparison stage adds diagnostic cycles; these
remain simulation measurements, not DDR bandwidth or physical-board results.

The [summary](vendor_pipeline/summary.json) retains all original fields,
including 16 source hashes, 362 generated-file hashes and `source_dirty: true`,
with LF line endings. The [console excerpt](vendor_pipeline/console_excerpt.txt)
retains all 18 warnings, calibration and pass markers, plus separate raw and
LF hashes. No `ERROR` or `CRITICAL WARNING` appeared. The same FAST calibration,
ideal interconnect and unqualified warm-reset limits apply. The original
vendor pass and failed route remain separate historical evidence.

## Registered comparison: routed timing and bitstream

The updated diagnostic meets its 100 MHz routed constraints. The
[build manifest](routed/build.json) identifies build `0xc63e39aa` and bitstream
SHA-256 `089061f1a6d83aadcd2cf236cf24c4a677f7657122f61e11454ced479a04e2e0`.
The bitstream is a local build artifact. This routed record predates the
physical check recorded below; its saved fields remain unchanged.

| Quantity | Recorded result |
| --- | ---: |
| Worst setup slack / failing endpoints | +0.642 ns / 0 |
| Worst hold slack / failing endpoints | +0.027 ns / 0 |
| Pulse-width failing endpoints | 0 |
| Bus-skew worst slack / passing constraints | +8.693 ns / 10 |
| Routable nets / fully routed nets / routing errors | 12,515 / 12,515 / 0 |
| LUTs / registers | 6,717 / 6,175 |
| RAMB36 equivalents / DSPs | 0 / 0 |

The [worst setup path](routed/worst_path.txt) is now in packet decoding and
CRC control, from `transport/state_reg[4]` to `transport/crc_reg[21]`:
8 logic levels and 8.722 ns data-path delay, with 1.676 ns logic and 7.046 ns
routing. The registered diagnostic comparison removed the former worst path.
The [complete timing report](routed/timing.txt), [utilization](routed/utilization.txt),
[constraint checks](routed/check_timing.txt), [bus-skew summary](routed/bus_skew_summary.txt)
and [result summary](routed/summary.json) retain the measured implementation.

The [reset review](routed/reset_review.txt) traces `CPU_RESETN` to one MIG PLL
reset and 43 MIG reset-chain preset pins, with no application data or enable
endpoint. Its generated reset exception resolves in the implemented design;
no additional timing waiver was added. CDC has no critical finding. Its 353
SmartConnect/XPM payload warnings and two MIG reset-chain warnings, along with
generated clock-buffer/reset/placement warnings, remain visible in the
[CDC](routed/cdc_summary.txt), [DRC](routed/drc.txt) and
[methodology](routed/methodology_summary.txt) records. They do not turn the
failed warm-reset probe into a qualification.

Vivado completed timing and bitstream generation, but the original Python
runner exited 1 because regeneration changed the date comments in three MIG
XDC files. The [separate verification](routed/verification.json) reconstructed
their exact original raw bytes by changing only those timestamps. All other
generated files, DDR settings/pins and the 15 other build inputs matched;
the original builder was checked against its archived hash. This explicit
verification exited 0 and produced the manifest while retaining the original
failure log, source hashes and build ID. The newer verifier has a separate
hash; the original run is not relabeled as a successful CLI invocation.

Future builds ignore only the strict header date in those three named MIG
files and record their raw hashes. [Six focused tests](routed/provenance_tests.txt)
check that constraints, pins, line endings, unrelated files/comments and
changed source inputs still fail verification.

## First physical DDR check

On 2026-10-02, build `0xc63e39aa` passed three diagnostic runs on the Nexys
A7-50T. The operator confirmed JP1 in JTAG mode, board power OFF/ON immediately
before programming, and J6 USB connected. That preparation is operator-reported;
the [Vivado log](board/program_log.txt) independently records target
`Digilent/210292B40D95A`, device `xc7a50t`, successful startup and the selected
DDR diagnostic bitstream. Programming and the host check both exited 0.

| Seed | Diagnostic cycles at 100 MHz | Read beats | Write beats |
| --- | ---: | ---: | ---: |
| `0x12345678` | 10,603 | 1,024 | 648 |
| `0xb06bd031` | 10,615 | 1,024 | 648 |
| `0x4ea349ea` | 10,642 | 1,024 | 648 |

Every run returned status `0x15` (READY, DONE and DDR_READY), error code 0,
and the expected transfer counts. UART reported zero retries and zero rejected
frames. The host checked ID, version, build ID and core clock over COM11 at
115200 baud; the local bitstream hash matched the manifest. The saved
[JSON](board/results.json), [CSV](board/results.csv), [test console](board/test_console.txt)
and [physical summary](board/summary.json) preserve all results, source identities
and original/saved artifact hashes.

Each run initializes 4,096 bytes across 16 selected locations, including both
ends of the DDR window, applies masked overlays, and compares every initialized
word twice. These three runs establish that bounded diagnostic on this board.
They do not establish a full 128 MiB sweep, a sustained repeatability result,
DDR bandwidth, warm-reset behavior or DDR-backed GEMM. Diagnostic cycle counts
exclude UART/host time; the separate wall-time fields include host polling.

## Pending evidence

- Broader memory coverage, sustained repeatability and representative bandwidth
  measurements. The compute array is not connected to this DDR diagnostic.
- Warm-reset qualification with clean DDR2 clock and CKE timing; the recorded
  abrupt-reset probe still fails this requirement.

## Source identity

The saved SHA-256 source maps identify the tested working-tree inputs after
UTF-8/LF normalization. Curated text artifacts also use LF; saved-artifact
hashes cover those bytes, while hashes labeled original, local or full-console
retain the unmodified run artifacts. These maps do
not imply a clean Git checkout. The vendor run records checkout
`35f8af4a7a04f6e8ef1d55f676fcef19455364cc` with `source_dirty: true`, so that
commit alone does not identify the tested changes. A later commit can be
associated with the run after its input hashes match. The portable aggregate
records 35 distinct source
fingerprints; the reset probe separately fingerprints its platform inputs and
vendor memory model. The historical reset fixture differs from the subsequent
cold-start fixture.
