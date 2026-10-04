# Selectable DDR overlap integration

2026-10-04. The production packet/register subsystem now selects a validated
overlap job shell with `ENABLE_OVERLAP=1`. VERSION=0x200 accepts serial MODE=0
and overlapping MODE=1; the default VERSION=0x100 hierarchy remains available.
The same mathematical, descriptor, transport and memory-safety contracts apply
to both modes. See [the architecture and counter contract](../../docs/ddr-overlap.md).

## Portable qualification

The initial integration checkpoint passes all 154 tests: 1,056 complete GEMM
jobs and 322,799 compared INT32 outputs. Its input identities predate the
fault-control timing correction below; saved fingerprints remain unchanged.
The shell and packet suites cover P=4/8, T=8/32 and READ_SLOTS=1/4.
The 800 seeded shell jobs supplement directed validation, final-response and
fault tests. This is behavioral AXI RAM evidence, separate from physical DDR.

| Suite | Configurations | Tests | Complete jobs | Compared outputs |
| --- | --- | --- | --- | --- |
| Validated job shell | Eight P/T/read-depth builds | 48 | 912 | 256,544 |
| Existing packet contracts on the selectable path | Eight builds, MODE=0 | 80 | 64 | 10,232 |
| Matched MODE=0/1 packet jobs and ownership checks | Eight builds | 16 | 72 | 53,704 |
| Default serial hierarchy compatibility | P8/T32, four reads | 10 | 8 | 2,319 |

The shell rejects invalid/overlapping allocations before issuing memory work,
holds START responses through backpressure, preserves actual final-B timing,
and freezes counters at the first fatal edge while obligations drain. The
packet tests upload independently generated matrices and read back every
result, input and guard byte. They exercise exact START replay, sequence
conflicts, busy host/configuration commands and a final B held for 100 cycles.
Matched modes use identical input bytes and checked transfer counts; the
overlap mode exhibits simultaneous compute and memory activity.

[record.json](record.json) seals source identities and all saved artifacts.
[execution.json](execution.json) and the individual records retain actual
process return codes. Each suite contains its runner summary, per-configuration
coverage and raw `results.xml`. [console_excerpt.txt](console_excerpt.txt)
contains the result tables and compile-check output. Full simulator logs and
generated executables remain in ignored `build/`.

Reproduce the current integration checks in Linux/WSL:

```sh
make lint test-ddr-overlap-job test-ddr-overlap-core PYTHON=.venv/bin/python
python scripts/test_ddr_core.py --only p8_t32 --read-slots 4 --build-dir build/ddr_serial_compatibility
```

Each runner refuses failed, skipped or incomplete cases and checks source
identity around execution. Use fresh output directories to retain prior runs.

## Host and build gates

[Software tests](unit_checks/record.json) pass on Windows and Linux: 77 per
platform. They cover signed packing, selectable-mode capability checks,
simulation/build identity, programmer gates and the board-runner packet
fixture. Software fixtures do not establish FPGA performance.

The updated host also completes a [physical compatibility smoke test](serial_host_smoke/record.json)
against the existing serial image `0xd558a543`: one 2x2x2 job, four compared
results, 752 checked guard/input/padding bytes, zero retries and 262 core cycles.
The board still runs VERSION=0x100; this run does not qualify FPGA overlap.

## Timing and physical scope

The [first route](initial_timing/README.md), build `0x2dd68757`, fits the 50T
but misses setup by 2.029 ns. Fault-code normalization fed a wide control
dependency. The corrected source separates scalar fault presence from the
diagnostic payload, preserving the cycle contract. Its
[fresh portable checkpoint](timing_predicate/README.md) passes the same
154-test suite under `0x36ffc81c`; the corrected route misses setup by
0.237 ns at 14 endpoints. Vendor and physical qualification continue.
The first vendor run was stopped after the route failed; its partial logs
remain in the ignored local archive and do not qualify that source.

The working serial image's performance remains documented in
[its board record](../ddr_gemm/read4/host_geometry/board/README.md).
New overlap throughput will be reported only after loading a qualified image
and comparing MODE=0/1 with matched multi-macrotile inputs. A 32x32 output at
T=32 contains one tile and cannot measure inter-tile overlap.
