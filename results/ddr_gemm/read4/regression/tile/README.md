# Tile DMA with selectable read depth

PASS: 56 test executions, 216 completed GEMM jobs, 21,938 compared INT32
outputs, and 128 invalid transfer requests. These are eight builds of the
production row planner, tile adapter, banked compute engine and AXI burst
engine against a behavioral AXI RAM.

| P | T | Read slots | Tests | Jobs | Compared outputs |
|---:|---:|---:|---:|---:|---:|
| 4 | 8 | 1 | 7 | 27 | 421 |
| 4 | 32 | 1 | 7 | 27 | 5,632 |
| 8 | 8 | 1 | 7 | 27 | 502 |
| 8 | 32 | 1 | 7 | 27 | 4,414 |
| 4 | 8 | 4 | 7 | 27 | 421 |
| 4 | 32 | 4 | 7 | 27 | 5,632 |
| 8 | 8 | 4 | 7 | 27 | 502 |
| 8 | 32 | 4 | 7 | 27 | 4,414 |

Python wide-integer matrix multiplication checks every useful output; full
memory comparisons check input contents, C padding, byte strobes and guards.
The transfer oracle enumerates byte addresses independently of the RTL.
R-channel ownership follows a FIFO of previously accepted AR commands, while
local data and terminal responses follow accepted commands with opaque tags.
Checks never associate a response with the current ARADDR signal.

The directed read tests establish:

- All four AR commands can be accepted before the first R beat.
- Completing AXI reads does not free ownership while bank delivery or local
  DONE is held. Metadata remains correct across rows, 4 KiB boundaries, slot
  reuse and the last legal DDR word.
- A read-response error preserves a fourth stalled AR after three earlier
  addresses were accepted; all four commands drain before operation DONE.
- An unaccepted local command can be canceled while two earlier accepted
  commands drain. No later operand writes or successful completion escape
  the fatal state.

The existing serial write, C gather, malformed read metadata, held operand
write, delayed B, and same-edge fault tests also run in both depths. The final
multi-read fault test has a depth-four-only body; depth one is covered by the
existing serial fault cases. Random stalls are exercised on independent AXI
and local channels. The saved workload/output counts are derived from this
run, not copied from older timing-dependent seeded regressions.

The current RTL exposes selectable read depth through DMA and the core.
This record qualifies the portable tile path only: it is not a full job/core,
vendor-model, routed-timing or board result. The separately measured
[P8 serial board checkpoint](../../../p8_serial/board/README.md) uses one read
slot and an earlier sealed source identity. Its hardware results do not
measure this four-read implementation, and no speedup is claimed here.

Reproduce all eight configurations with the pinned test dependencies:

```text
python scripts/test_tile_dma.py --build-dir build/test_tile_dma_read4
```

[summary.json](summary.json), eight coverage/XML pairs, and
[console.txt](console.txt) are preserved byte for byte.
[manifest.json](manifest.json) binds all 18 saved artifacts to their original
paths and records 16 source fingerprints plus the original tool versions.
The runner's before/after hashes and the curation-time source check agree.
Earlier serial evidence remains unchanged; these portable results do not
establish a committed source revision or qualify a new bitstream.
