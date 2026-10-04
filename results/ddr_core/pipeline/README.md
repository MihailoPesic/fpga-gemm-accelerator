# DDR core regression after timing changes

October 2, 2026: all four array/tile configurations pass six packet-level
integration tests after registering row-burst decisions, macrotile boundary
flags and watchdog thresholds. The source fingerprints match across every
run. This checkpoint supersedes the earlier [20-test result](../summary.json)
for the current RTL.

| P | T | Tests | Completed GEMM jobs | Compared outputs |
| --- | --- | --- | --- | --- |
| 4 | 8 | 6 | 7 | 224 |
| 4 | 32 | 6 | 7 | 2,288 |
| 8 | 8 | 6 | 7 | 240 |
| 8 | 32 | 6 | 7 | 2,304 |
| Total | | 24 | 28 | 5,056 |

The production packet transport, register bank, host memory owner, serial
job controller, tile DMA, local banks, array and AXI burst engine run against
behavioral AXI RAM. All A/BT uploads, START/configuration commands and result
downloads use framed packets. The Python-integer oracle compares every matrix
value and checks input preservation, C padding and external guards. Job cycles
end at the final successful raw B handshake; arithmetic and transfer counters
match independent shape formulas. The full suite also retains response errors,
independent channel stalls, replay/conflict checks and outstanding-transfer
drain tests.

The added watchdog regression checks the exact first fault edge at limits
1, 2, 3, 7 and 16. It opens a stalled AR channel so a real address handshake
occurs on the limit-8 expiry edge: progress prevents the fault, and eight
subsequent idle edges with R stalled produce WATCHDOG. The oracle counts
pre-edge local and AXI handshakes without reading or forcing the RTL timer.
Every timed-out read retains AR and drains after the stall is released;
RESET_REQUIRED remains set. A zero register limit is rejected, and a read
with limit `0xffffffff` completes successfully. The maximum-limit check does
not simulate billions of cycles to exercise counter saturation.

The host watchdog now stores `max(limit,1)-1` at memory admission. Its counter
starts at zero, resets on progress and otherwise advances by one, so equality
detects the same first expiry as the previous comparison. Counter saturation,
first-fault priority and drain state transitions are unchanged. Idle admission
uses only the latched host fault and memory-fault input; active protocol and
watchdog terms cannot apply while the host memory owner is idle.

The four runs exchanged 3,661 requests and responses. Their combined raw AXI
traffic was 14,368 read beats, 14,748 write beats and 1,317 write responses;
these totals include host transfers and are not GEMM performance counters.

[`summary.json`](summary.json) records the exact sources, versions, totals,
original log hashes and hashes of every saved artifact. Per-configuration
summaries, coverage and XML were copied byte-for-byte. The
[`console excerpt`](console_excerpt.txt) retains regression results and
diagnostic lines; the complete logs remain under ignored
`build/test_ddr_core_pipeline/`. Deliberate memory-error injections and tool
deprecation notices are retained.

Current portable Icarus syntax/elaboration checks also pass. Their
[`summary`](compile_summary.json) and [console](compile_console.txt) are exact
copies with matching current source hashes. They are not comprehensive static
lint or physical timing analysis.

Reproduce the packet regression in the pinned Linux/WSL environment:

```text
.venv/bin/python scripts/test_ddr_core.py --build-dir build/test_ddr_core_pipeline_repro
```

This evidence covers byte-framed commands and behavioral AXI memory. UART pin
timing, vendor DDR models, full routed timing and physical DDR GEMM require
separate results. The [subsystem contract](../../../docs/ddr-core.md) defines
ownership and fault behavior; overlap and four-read concurrency remain outside
the serial release.
