# Four-read production build

Build `0xd558a543` passes the gated P8/T32, READ_SLOTS=4 production
route and bitstream generation at 100 MHz: setup +0.203 ns,
hold +0.014 ns. [Summary](summary.json) identifies the
bitstream, vendor simulation and all 13 physical reports.

The [read-only reset review](review/checkpoint_identity.json) checks a
byte-identical copy of this production checkpoint: the exact generated
MIG reset selector reaches 43 preset pins and one PLL reset, matching
CPU_RESETN with no unexpected endpoints. The source checkpoint remains
unchanged. [Qualification checks](review/qualification_checks.json)
bind live source, generated configuration, reports and image identities.
The [manifest](manifest.json) seals all copied evidence.

These build and checkpoint checks did not program an FPGA. Subsequent
[cold-start board qualification](../board/README.md) passes all 48 jobs, every
output and allocation guard. The [physical comparison](../../board_comparison/README.md)
records measured performance separately from these routed checks.
Generated bitstream and DCP files remain under ignored
`build/gemm_p8_read4_host_geometry/`.
