# Serial DDR packet subsystem verification

## Current regression after timing changes

The [pipeline regression](pipeline/README.md) passes all 24 tests across the
four P/T configurations, with 28 completed jobs and 5,056 compared outputs.
It covers the registered row-burst decisions, macrotile boundary flags and
watchdog thresholds, including new exact-edge watchdog checks. Current compile
evidence and common source fingerprints are saved with that checkpoint.
Vendor simulation, routed timing and board qualification remain separate.

## Historical regression before timing changes

The original 20-test summary and artifacts below remain unchanged and describe
the earlier source fingerprints.

October 2, 2026: the byte-framed command path connects the production packet
transport, register bank, job controller, tile DMA, banks, array and AXI burst
engine to behavioral AXI RAM. All four P/T builds pass five tests each.

| P | T | Tests | Completed GEMM jobs | Compared outputs |
| --- | --- | --- | --- | --- |
| 4 | 8 | 5 | 7 | 224 |
| 4 | 32 | 5 | 7 | 2,288 |
| 8 | 8 | 5 | 7 | 240 |
| 8 | 32 | 5 | 7 | 2,304 |
| Total | | 20 | 28 | 5,056 |

Matrices are uploaded as A and explicitly packed BT through real MEM_WRITE
packets. Register commands configure and START the job, STATUS polling observes
completion, and MEM_READ downloads every C value. An independent Python-integer
oracle checks all outputs and input/padding/guard bytes. Shapes include M/N>T,
non-square/odd tails, K=1,7,8,9,255,256 and signed extrema. Job traffic and
compute counters match independent formulas; JOB_CYCLES ends at the actual
final successful AXI B handshake. Host reads/writes preserve frozen nonzero
job counters, DONE and last-job identity.

Every build covers all 30 host transfer lengths from 8 to 240 bytes, 16-beat
and 4 KiB splits, the final DDR-window word and 24 invalid memory commands
without AXI effects. Independent address/data channel stalls exercise the
shared-memory owner. Duplicate writes/START replay without execution; changed
same-sequence requests conflict. Corrupt/truncated frames cause no effects.
Busy host writes also leave their sentinel bytes unchanged.

Read/write response errors, a held-AR watchdog timeout and calibration loss
during accepted write collection are tested separately. After a fatal response,
diagnostic registers remain accessible while memory ownership is retained.
Releasing the stalled channel drains the outstanding transfer without
withdrawing AXI VALID or reporting success. Calibration loss after the first
host write word still drains all captured original words/strobes through
independent AW/W/B stalls. Reset remains required afterward.

The independent host-library suite passes 28 tests on native Python 3.13.0
and Linux/WSL Python 3.12.3. It uses the real framing/retry Link with a software
command model; no serial device is opened. Tests cover signed BT serialization,
descriptor/guard bounds, all geometries, reset/error handling, frozen counters
and CLI JSON/CSV failure records. These are separate from the RTL scoreboard.

Reproduce on Linux/WSL with the pinned environment:

```text
make test-ddr-core PYTHON=.venv/bin/python
```

[`summary.json`](summary.json) records exact tested sources, configuration
identities, seeds, tool versions and saved artifact hashes. Per-build coverage
and XML are preserved unchanged. Native and Linux host evidence is in
[`host_tests.json`](host_tests.json) and
[`host_linux_tests.json`](host_linux_tests.json).
The [subsystem contract](../../docs/ddr-core.md) and
[host API](../../docs/host-gemm.md) explain ownership and measurement semantics.
The existing compute, banks, job controller, registers and burst engine remain
unchanged from their earlier verified checkpoints.

This result covers byte-level packet transport and behavioral AXI memory.
It does not establish UART pin timing, SmartConnect/MIG/physical DDR behavior,
routed subsystem timing, physical DDR GEMM, overlap or four-read concurrency.
The serial version is `0x00000100`; it is not full DDR v1 compliance.

The first testbench run used an incorrect timing assumption for the large-tile
busy test: it searched for a result AW before compute had reached that stage.
The permanent test now waits for the actual first result AW under held B.
Final configurations use one corrected test-source fingerprint; the prior
test source and failed log remain under ignored local `build/`.
