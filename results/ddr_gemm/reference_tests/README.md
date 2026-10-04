# Vendor reference checks

The corrected reference calculation passes 194 arithmetic cases and 776
little-endian byte checks in both native XSim 2026.1 and Icarus under WSL.
This tests the actual functions extracted from the vendor fixture, without
the accelerator, DDR controller or memory model.

The runner checks independently calculated Python integer results for the
two vendor jobs, directed K boundaries through 256 and 128 seeded cases.
It also executes the fixture's early self-check against all 16 literal
results. The native [simulation console](xsim/command_2.txt) records:

```text
DDR_GEMM_ORACLE_PASS jobs=2 outputs=16
VENDOR_REFERENCE_PASS cases=194 bytes=776
```

XSim uses `--debug off --relax --mt auto`, matching the full vendor run.
The result follows the [isolated failure investigation](../vendor_oracle_failure/README.md):
explicit operand/product intermediates avoid the unknown result observed
with the original nested expression. This is a demonstrated simulator
behavior and tested workaround, not a vendor-confirmed compiler diagnosis.

```text
python scripts/test_vendor_reference.py --simulator iverilog
python scripts/test_vendor_reference.py --simulator xsim
```

Use `--vivado-bin` if the XSim tools are outside PATH and the default Windows
installation. The [manifest](manifest.json) records the matching fixture and
runner source hashes, all seven saved artifact hashes and both completion
markers. Per-simulator summaries and complete command consoles are copied
byte-for-byte. Their original artifact maps also record the generated harness,
which remains under ignored `build/` and is reproduced by the runner.

These checks qualify the reference calculation. The failed full-system run
remains a failure; complete UART/MIG simulation, routed bitstream qualification
and physical-board validation are separate gates.
