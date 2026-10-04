# Concurrent local-buffer ports

`gemm_tile_engine` now has an opt-in `CONCURRENT_PORTS=1` interface. A local
compute job owns its selected input and output sets until completion. DMA may
fill the other operand set and read the other completed result set during that
job. Pending and stalled read responses keep their accepted buffer identity;
START cannot replace that result set until the edge after response consumption.
The default remains zero, and the production DDR controller still rejects MODE=1.

| Executed check | Result |
| --- | --- |
| Concurrent ports, P4/P8 and T8/T32 | 12 tests; 36 jobs; 6,817 computed outputs checked with a Python integer oracle and complete result-matrix readbacks |
| Default serial ports, all four geometries | Eight result-bank/local-engine tests; 164 matrix jobs; 23,360 compared outputs |
| Default serial packet core, P8/T32 with four reads | Ten tests; eight GEMM jobs; 2,319 compared outputs, page-boundary transfers, protocol/fault and ownership checks |
| Icarus syntax/elaboration checks | Native wrapper returns zero; [console](lint_console.txt) retains compiler warnings and PASS markers |
| Private ownership mutations, P4/T8 | Three deliberately broken implementations detected by their expected assertion failures |

The concurrent tests include different input/output buffer IDs in both
directions, simultaneous load/read/compute edges, nonzero input padding,
odd result tails and unchanged launch/drain cycle counts. Active-buffer
requests remain blocked through 79 observed launch-gap edges. The suite
checks 3,873 stalled-response edges, pending and held responses across
disjoint STARTs, same-buffer reuse on the consume edge, invalid result reads,
reset and signed -128 multiplication. These are exercised events, not an
exhaustive state-space claim.

The private mutations replace the saved response owner with the live request
buffer, allow reads from the computing output buffer, or release a response
owner before its handshake edge completes. Each mutated design compiles and
runs all three tests; exactly its expected test fails. The copied
`expected_failure.xml` files are defect-detection evidence and are excluded
from the count of 30 normal passing tests. Production RTL was unchanged by
the mutation runs.

[record.json](record.json) binds copied summaries, XML, coverage, actual process
exit records and source hashes. [source_delta.patch](source_delta.patch) compares
the new engine with the archived baseline engine. The baseline's normalized
source hash matches the qualified manifest for build `0xd558a543`.
No other input to that board build changed.
This source revision has no new vendor simulation, route or board measurement.
The [working FPGA image](../ddr_gemm/read4/host_geometry/board/README.md)
retains its original source and bitstream identities.

```text
make test-tile-overlap PYTHON=.venv/bin/python
make mutation-tile-overlap PYTHON=.venv/bin/python
```

See the [local-port contract](../../docs/tile-engine.md#commands-and-ownership).
Full DDR overlap still requires separate read/write DMA operation contexts,
tagged tile records and a scheduler that owns both input and result sets.
