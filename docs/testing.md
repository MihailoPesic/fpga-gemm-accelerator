# Build and test

## Portable tests

Requires Linux/WSL, Python 3.12, make and Icarus Verilog 12.0. Python dependencies
are pinned in [requirements-test.txt](../requirements-test.txt). No AMD IP or
Vivado is used by the portable tests. Saved result manifests record the exact
runner and embedded-Python versions used for each run.

On Linux/WSL with Python venv support, make and Icarus installed:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
make lint test mutation PYTHON=.venv/bin/python
```

From PowerShell, after that environment exists in Ubuntu/WSL:

```powershell
wsl -d Ubuntu -- make lint test mutation PYTHON=.venv/bin/python
```

`scripts/test.py --only pe|p4|p8` selects one suite. Failed RTL compilation,
failed/skipped/missing test cases or a bad result XML cause a nonzero exit.
Logs, per-configuration coverage and a source-hashed summary go under
`build/test/`. Mutation tests modify isolated copies under `build/mutations/`;
a compiler failure does not count as a detected defect.

The PE test exhausts all signed operand pairs and checks each accumulation
commit. The microtile test compares every drained result against Python
integer dot products, checks exact feed/drain/completion edges, exercises
every rows/cols tail pair, K boundaries, invalid commands, busy starts,
changed configuration while busy, immediate next launch and resets during
feed/drain. Reduction tags assert inside the simulated mesh.

The `lint` target is an Icarus syntax/elaboration/warning check, not a claim
of comprehensive static RTL lint or formal proof. Public CI is not yet run.

## Vivado core check

```sh
vivado -mode batch -notrace -source scripts/synth_core.tcl
```

This synthesizes and routes both complete microtile cores for
xc7a50ticsg324-1L, enforces P squared DSPs and nonnegative setup/hold slack.
Results go to `build/synth_core/`. The final harness places registers on every
core input and output, on the same 100 MHz clock, with 0.2 ns uncertainty and
HD.CLK_SRC=BUFGCTRL_X0Y0. All register paths through the core and its modeled
neighbors are timed. Paths from external harness pins into the producer
registers and from consumer registers to external pins are deliberately
excluded. Those pins do not model board I/O. Consequently check_timing reports
false-path I/O exclusions; it must show zero unconstrained internal endpoints.
Out-of-context port-routing warnings are expected for those excluded pins.
This is a core feasibility check, not full GEMM or board timing closure.

The first unregistered-boundary attempt failed at the drain-control/output
path under a 2 ns external-delay budget; its source and report are preserved
in `results/core/initial_timing/`. The revised controller registers drain state
without changing the external cycle contract. A later I/O minimum-delay model
also exposed the mismatch between ideal external arrival times and routed
clock insertion delay. The final registered harness measures the intended
same-clock neighboring logic explicitly. Results from the different harnesses
must not be presented as a controlled before/after performance comparison.

## Native-DDR baseline

```sh
vivado -mode batch -source scripts/build.tcl -tclargs -synth-only
```

The repaired scripts explicitly select the real existing sources and import
the two saved XCI configurations into `build/native_ddr`. Board version 1.3
must be installed; `NEXYS_BOARD_REPO` can name its repository. Scripts fail
on missing/mismatched board identity or locked IP; no download occurs silently.
The source clock belongs to the generated clock wizard XDC; baseline synthesis
checks one primary clock on CLK100MHZ. Native RTL receives no GEMM modules.

`-no-bitstream` additionally implements and writes reports; the default asks
for a native baseline bitstream after timing and critical-violation checks.
This remains the old protocol. No `make bitstream`, `make hw-test`,
`make sim-vendor`, `make formal` or `make bench` is advertised as implemented
for GEMM until its relevant gate is delivered. A passing native UART loopback
is not a MIG vendor-memory simulation.

## Operand banks and prefetch

`make test` also runs `scripts/test_memory.py`. To select only that regression:

```sh
make test-memory PYTHON=.venv/bin/python
```

It checks the memory wrapper with P=4/8 and T=8/32, including all bank groups,
word-boundary prefetch, input-buffer exclusion and reset. Source hashes,
per-configuration coverage and XML results go under `build/test_memory/`.
The compute core and its original regression are unchanged.

```sh
vivado -mode batch -notrace -source scripts/synth_memory.tcl
```

This checks that operand storage infers block RAM and the wrapped array keeps
one DSP per PE. Reports go under `build/synth_memory/`. It does not route the
wrapper or establish its timing. See [memory.md](memory.md) for the interface
and added prefetch latency.

## Complete local matrix engine

`make test-tile PYTHON=.venv/bin/python` checks result-bank mapping and full
matrix jobs for all four P/T combinations. `make test` includes this suite.
Reports and source hashes go to `build/test_tile/`.

`make synth-tile` runs the registered-neighbor timing harness for P4/P8,T32
at 100 MHz, with 0.2 ns clock uncertainty. It checks setup and hold and writes
reports to `build/synth_tile/`. External harness-pin paths are excluded;
all internal register/BRAM/DSP paths must be timed. The script attempts
[post-route hold fixing](https://docs.amd.com/r/en-US/ug835-vivado-tcl-commands/phys_opt_design)
if needed and fails when negative setup or hold slack remains.
See [tile-engine.md](tile-engine.md) for the interfaces and counter definitions.
The [saved September 30 run](../results/tile_engine/README.md) fails P4 hold
timing; P8 is not reached. Simulation success does not satisfy this timing gate.
