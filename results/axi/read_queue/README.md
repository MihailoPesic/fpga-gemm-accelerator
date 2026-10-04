# One-slot and four-slot AXI read regression

`gemm_axi_burst` passes 17 portable simulation tests: four existing tests with
`READ_SLOTS=1`, and the same four plus nine queue tests with `READ_SLOTS=4`.
The simulator was Icarus Verilog 12.0 with Python 3.12.4 and cocotb 2.0.1.

The four-slot subset accepts 354 local read commands, including 54 invalid
descriptors. It completes 350 commands; a coordinated-reset test deliberately
discards four accepted commands in mixed states. Thus accepted minus
reset-discarded equals completed. The tests check 1,678 delivered words against
an independent tag/index/data scoreboard and observe 1,826 AXI R beats. A seeded sequence
contains 160 commands: 144 valid and 16 invalid. Counts for the existing RAM,
write-strobe and protocol tests remain separate in each configuration's coverage.

Coverage includes all burst lengths 1..16 and both local ownership and AXI
outstanding occupancy 0..4. Directed checks exercise four AR handshakes before
any R response; full response buffering while head data/DONE is stalled;
ordered valid/invalid/valid commands; repeated queue wrap; simultaneous slot
retirement/allocation and AR/old-RLAST; RRESP, RID and early/late RLAST faults
at every queue position. Held AR and validated output survive unrelated faults,
unoffered work cancels, and missing RLAST cannot erase later issued obligations.
Additional checks cover a held successful DONE during a later protocol fault,
coordinated reset with all four slots occupied, and 72 ordered commands with
repeated opaque tags and consecutive invalid descriptors. The monitor records
48 simultaneous retirement/allocation edges and ten new-AR/old-RLAST edges.

Slot ownership lasts through the ordered local DONE handshake. `axi_quiescent`
tracks external transaction obligations, while `local_idle` also accounts for
buffered results, held completions and write-data collection. Tests distinguish
these states explicitly and use public handshakes rather than internal RTL state.

[summary.json](summary.json) and the per-configuration summaries retain the six
tested source hashes. [manifest.json](manifest.json) binds all nine unchanged
execution artifacts, including both XML reports, coverage and the complete
[console](console.txt). The console retains simulator VPI and library deprecation
warnings. No tests were rerun during evidence collection.

This is primitive-level simulation evidence. The current board scheduler and
adapter do not yet use four outstanding reads. This record establishes no
four-slot system integration, routed timing, resource count, board performance
or formal proof. The [integration regression](integration/README.md) separately
checks the current GEMM, tile-DMA and diagnostic instances with their default
`READ_SLOTS=1`. Earlier AXI and physical-board records remain separate. The
original 14-test run is preserved in the private build directory identified by
the manifest; this record contains the final expanded run.

```text
python scripts/test_axi_burst.py --build-dir build/read_queue_reproduce
```
