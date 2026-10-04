# Build and programming evidence gates

The native Windows Python unit run passed all 26 tests with exit code 0.
Source hashes were captured before execution and rechecked after the run and
the separate archived-image preflight. The complete stdout/stderr stream and
per-test identities are retained in [console.txt](console.txt) and
[summary.json](summary.json); [manifest.json](manifest.json) binds the saved bytes.

The tests use synthetic simulation, timing, report and bitstream fixtures.
Vivado calls are mocked. They check malformed or stale evidence, missing and
modified artifacts, source/configuration identity, build settings, propagated
process failures, and read-depth agreement between simulation and bitstream
stages. READ_SLOTS=4 changes BUILD_ID and cannot reuse a READ_SLOTS=1 simulation.
This is software-gate evidence, not a new RTL simulation or physical build.

The read-only [archived preflight](archived_preflight.json) also passes for the
previously qualified P8/T32 image `0x01caf61c`. That saved manifest predates the
read-depth field; an absent field is interpreted as READ_SLOTS=1 in both its
simulation and bitstream identities. Its selected bitstream, simulation record
and all 13 physical reports were hash-checked without requiring the current
checkout to match the archived source set. No board was programmed or queried.

```text
python -m unittest -v tb.test_build_ddr_gemm tb.test_program_ddr_gemm
```
