# Four-cycle result gathering: portable profile comparison

All 16 paired jobs pass at P4, T8/T32, with the same unstalled and explicitly
delayed behavioral AXI RAM models as the [original profile](../../profile/README.md).
Only `rtl/memory/gemm_tile_dma.sv` differs among the 20 recorded source inputs.
The successful path skips the result-pair preparation state; existing memory,
bank-response and fault contracts remain under their separate regressions.

The original run included uncommitted source files, so its commit ID alone
does not reproduce the baseline. `change.patch` preserves the precise
baseline-to-current RTL delta. Reversing that patch against the current
module reconstructs the baseline SHA256
`6a783e79ab1fd7ab94c1e39034e902cafd637230345ee4177eecf15c2fa97816`.
This reconstruction was checked in memory without modifying project sources.
Reproduction requires the other 19 inputs and tool versions recorded by
both manifests, as well as the matching tile-DMA version; the patch does not
reconstruct unrelated historical working-tree content.

Every job saves exactly one cycle per 64-bit write beat. For the T32 dense
32x32x256 case with the unstalled model, JOB_CYCLES falls from 25,648 to 25,136;
the C-store phase falls from 3,272 to 2,760 cycles. Computation, read/write
traffic, burst addresses, lengths, byte masks and all other burst intervals
are unchanged. This is a simulation result, not a measured board speedup.

The independent comparison checks all ordered burst metadata and per-word
relative event times from the complete local traces. Each original trace is
bound to the previously published compact evidence by its recorded hash.
Both runs compare every output and check full input allocations, C padding
and allocation guards: 25,156 outputs and 384,000 checked memory bytes per run.

`comparison.csv` gives the 16 before/after results. `comparison.json` records
the scope, source difference and independent checks. Other JSON/CSV/XML files
are byte-for-byte copies produced by the profiler runner. Full event lists
remain in the local build directories; their hashes are preserved in
`compact_summary.json`. `manifest.json` hashes every saved evidence file.

This checkpoint contains no new MIG simulation, routed design, bitstream or
physical measurement. Those require the separate platform and board gates.
