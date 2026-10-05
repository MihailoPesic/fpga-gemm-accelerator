# Controlled operand reuse and overlap

P8 at 100 MHz, READ4 and 1 Mbaud. A uses T8/MODE0; B uses T32/MODE0;
C uses T32/MODE1. All 16 shapes contain 30 completed samples per series:
480 T8 jobs and 960 T32 jobs. B and C use one identical T32 bitstream.
Core32 and host6 source identities, shapes, seeds, A/B mathematical input
digests, descriptors and freshly initialized C sentinel digests match.

Three figures, each saved as PNG and PDF, and the CSV are original outputs
of the source-bound collector.
The collector's native completion receipt records its actual exit and tool
chunk. Its session ID is null when the command completed directly, or the
actual positive session ID when it yielded. Board grid receipts retain
their independently checked actual yielded session IDs.
Throughput uses useful operations divided by JOB_CYCLES through the final
successful DDR write response. Inputs remain resident between samples;
packing, input upload, C initialization, host transfer and validation times
are retained separately. Error bars show the minimum/maximum around each
series median. Speedup tables use a ratio of cycle medians, rather than
the median of per-sample speedups. Traffic counts accepted AXI beats and
useful write strobes; they do not measure physical DDR bus overhead.

The two referenced grid packages preserve every original case/job record,
seal and actual completed parent receipt. They are not duplicated here.
Their verifiers must pass before this verifier independently checks the
matched signatures, tile traffic, distributions, ratios and table data.
No per-job raw matrix bytes or UART transcript were retained for the
grid, so this replay does not reproduce its numerical output comparisons.
The T32 recovery package retains the earlier failed parent separately;
copied completed maximum/dense units are explicitly identified as reused.

From this directory, with both sibling grid packages available:

```text
python -B -O verify.py --t8 ../board/t8/benchmark --t32 ../board/t32/benchmark
```

The command is software-only. Source checks additionally require
`--check-current --repo PATH_TO_CHECKOUT`.
