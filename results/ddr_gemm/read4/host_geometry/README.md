# Registered host burst geometry

The board-qualified P8/T32 four-read build has ID `0xd558a543`. Host commands use a
registered burst length prepared at validated packet admission or the previous
burst's successful nonfinal completion. This separates length planning from
command validation without changing the burst schedule or adding a controller
state. The registered first-error response boundary remains in place.

The [portable record](core/record.json) contains 80 passing packet/core tests
across READ_SLOTS=1/4 and P4/P8 with T8/T32: 64 complete GEMM jobs and 10,232
compared outputs. Copied summaries, XML and coverage bind the actual runs to
their source and artifact hashes. No failed or skipped tests count as passes.

The page-tail test runs all 16 aligned starts in a page's last 128 bytes, with
five transfer lengths, in every configuration. Independent word enumeration
checks AR/AW burst schedules, every first-burst cap from one to 16 beats,
response counts, memory bytes and guards. Selected cases hold AR/AW/W for
20 cycles and require stable payloads. Separate completion-edge fault cases
check empty, stable fatal replies and retained ownership while obligations drain.

The [timing experiment](route_probe/README.md) passes the full 100 MHz route
with setup/hold margins +0.203/+0.014 ns. It stops before bitstream generation.
The [vendor integration](vendor/README.md) also passes: two complete
framed-UART jobs, all 16 outputs, 65 packets and matching transfer counters
through SmartConnect, MIG and the unchanged DDR2 model. The native simulation
exits zero after its final PASS. The
[production bitstream](routed/README.md) also passes at 100 MHz with
+0.203/+0.014 ns margins. Its copied-checkpoint review confirms all 44 reset
endpoints and preserves the checkpoint hash. Source, generated platform,
simulation, bitstream and all 13 physical-report identities match.
The [physical board suite](board/README.md) passes all 48 jobs, 49,593 outputs
and 689,628 input/padding/guard bytes. Dense 32x32x256 median latency is
11,576.5 cycles, or 4.529 useful GOPS including DDR movement and the final B
response. Thirty dense runs have no mismatches or transport retries. This
image is now programmed on the board. The
[physical revision comparison](../board_comparison/README.md) records a 1.83834x
dense median speedup against the saved one-read P8 image, including the source
changes and unchanged traffic. Overlap and extended qualification remain
separate release work.
