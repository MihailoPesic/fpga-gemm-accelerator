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

Bare `make` prints the command guide without launching simulation or Vivado.
`make test-release` includes GNU Make dry-run checks of build/demo manifest
selection, quoted paths, overrides and legacy defaults. Those software checks
never open a serial port or run Vivado.

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
of comprehensive static RTL lint or formal proof. GitHub Actions runs the
portable checks on Ubuntu; the first published core/memory checkpoint passed.

The overlap integration has separate entry points:

```sh
make test-ddr-overlap-job test-ddr-overlap-core PYTHON=.venv/bin/python
```

The job-shell suite checks widened descriptor validation, both scheduling
modes, actual final-B timestamps, independently sampled compute/traffic/wait
counters, held replies, output-space stalls, calibration/watchdog faults and
fatal drain. It completes 100 seeded jobs per P/T/read-depth configuration;
regular larger shapes reach M,N <= 97. The packet suites run the existing
command/error regression against the selectable build, then compare matched
MODE=0/1 input images and complete memory readbacks. They exercise START replay,
BUSY host exclusion and a held final write response. Source identities and
raw XML/coverage go under ignored `build/`. See [the integration boundary](ddr-overlap.md).

For a smaller explicit smoke run:

```sh
.venv/bin/python scripts/test_ddr_overlap_job.py --only p8_t8 --read-slots 4 --random-jobs 3 --build-dir build/overlap_smoke
```

The smoke count is recorded in its summary and does not replace the full
100-job regression. Both serial and overlap core configurations are compiled
by `make lint`; GitHub Actions runs the full overlap suites per geometry.

To attribute serial DDR job cycles and compare T8/T32 reuse under explicit
behavioral memory responses:

```sh
make profile-ddr PYTHON=.venv/bin/python
```

`make profile-read-dma PYTHON=.venv/bin/python` compares read depths one and
four at fixed P8/T32, with identical matrices and traffic. It checks complete
outputs and guards, requires unchanged compute/store cycles and records
per-phase CSV plus source and artifact hashes. Its software-driven tile-helper
cycles are separate from the full job counter and physical DDR measurements.

`make test-tile-overlap PYTHON=.venv/bin/python` checks opt-in concurrent local
ports at P4/P8 and T8/T32. The standalone tile's default ports remain serial;
the selectable DDR build explicitly enables concurrent ports. The tests
check other-buffer load/read during compute, independent buffer IDs, complete
arithmetic/readback, ownership on START, response stalls and reset. Results
go to `build/test_tile_overlap/`; choose a fresh `--build-dir` to retain runs.
`make mutation-tile-overlap PYTHON=.venv/bin/python` runs three broken private
copies and requires their expected assertion failures. Compile/tool failures
do not count as detected defects. See [the saved evidence](../results/tile_overlap_ports/README.md).

The profiler checks complete outputs and guards, reconciles its event trace
with all frozen counters, and checks sensitivity to known per-burst delays.
See the [accounting convention](ddr-core.md#cycle-attribution). Results go to
`build/profile_ddr/`; select a new directory for each later experiment with
`scripts/profile_ddr.py --build-dir build/profile_ddr_run2`.
`--only p4_t8|p4_t32` and `--model unstalled|latency` select a subset.
These are simulation measurements, separate from the physical board results.

## Release board measurements

`make test-release test-formal-traces` checks project command routing, the
measurement runner, comparison collector and formal trace decoder using software fixtures. These
checks require neither the FPGA nor optional plotting packages and are included
in the portable CI job. They do not establish accelerator correctness.

`scripts/qualify_release.py` tests an already programmed, qualified P8 image.
It checks its bitstream, vendor simulation and routed reports before opening
the serial port. It never resets or programs the FPGA. The required board
startup sequence is in [ddr-board.md](ddr-board.md).
The following commands use the fresh `build/gemm_current` image from that
guide. Its matching simulation, bitstream and routed reports must be present;
the generated archive is not included in a clean clone. To test the preserved
image `0x9d4beb4d`, substitute its local
`build/gemm_release_p8_t32_1mbaud/build.json` manifest throughout.

```text
python scripts/keep_awake.py -- python scripts/qualify_release.py --port COM11 --manifest build/gemm_current/build.json --output build/release_checks --phase maximum --modes both
python scripts/keep_awake.py -- python scripts/qualify_release.py --port COM11 --manifest build/gemm_current/build.json --output build/release_checks --phase endurance --modes both --resume
python scripts/keep_awake.py -- python scripts/qualify_release.py --port COM11 --manifest build/gemm_current/build.json --output build/release_checks --phase benchmark --modes both --resume
```

Modes, seed, oracle and sample count are part of the immutable result plan.
Use a fresh output directory when changing them.
The combined `--phase all --modes both` command runs all three phases.
After a reconnect, inputs for each pending case are reloaded.

For a long Windows run, `scripts/keep_awake.py -- COMMAND` holds a temporary
`ES_CONTINUOUS | ES_SYSTEM_REQUIRED` request while its child runs, preserves
the child's output and exit status, and clears the request on exit. It changes
no power-plan settings. Explicit Sleep, closing the lid or loss of power can
still interrupt qualification; keep the laptop awake for the whole run.
On other platforms the wrapper simply executes the command.
See Microsoft's [execution-state API](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate).

The [first 1 Mbaud T32 endurance attempt](../results/ddr_overlap/release_1mbaud/board/t32/endurance_failed/README.md)
stopped with a READ_REG timeout after
381 complete jobs. Windows recorded Modern Standby and a later keyboard wake
during the pending job. A read-only reconnect found the same image, completed
job ID and frozen counters, and independently checked its 64 retained outputs
and all allocation guards. This supports host suspension as the interruption
cause; it does not qualify the failed run or prove indefinite link recovery.
The replacement endurance run starts from zero elapsed time in a new connection.

Maximum checks every output at M=N=1024,K=256 and retains compressed complete
memory snapshots. Endurance completes mixed workloads in both modes for at
least 1,800 seconds in one connection; reconnect gaps never count toward it.
Benchmark uses 30 samples per mode for the 12 square cases and four tails in
the specification. `--cases 11` selects 256x256x256; a subsequent matching
`--resume` can finish the other cases. Inputs remain in DDR within each case.
Every job starts with fresh C sentinels, then reads each A/BT/C allocation
once and compares all output, input, padding and guard bytes. Frozen FPGA
counters and host phases are recorded separately.

Only complete units with intact seals can be skipped on resume. A failed or
interrupted unit stays preserved and requires a fresh result directory; the
runner does not replay an uncertain START or clear a hardware fault. The
first mismatch, transport error or changed identity stops the run. The
runner's software tests use a synthetic device and are not board evidence.

The Make equivalents are `make bench` and `make hw-release`, with `PORT`,
native `PYTHON` and a fresh `RELEASE_OUTPUT`; both use the same temporary
sleep wrapper. `make demo` runs one small complete comparison.
`make bitstream BOARD=nexys_a7_50t` runs vendor simulation and implementation
in `RELEASE_BUILD`, defaulting to `build/gemm_current`, P8/T32, four reads,
selectable scheduling and 1 Mbaud. The current targets derive `MANIFEST` from
`RELEASE_BUILD/build.json`; set `MANIFEST` explicitly for an archived image.
Set `VIVADO` when it is not on PATH. Programming remains a separate cold-start
operation; the [board guide](ddr-board.md#program-and-run-the-new-build) uses
the same build directory for every stage.

After both builds have completed the same cases, collect the controlled
comparison with:

```sh
python -m pip install -r requirements-benchmark.txt
python scripts/collect_benchmarks.py --t8 build/t8_measurements --t32 build/t32_measurements --output build/controlled_comparison
```

T8 provides MODE0 samples; T32 provides matched MODE0/1 samples. The collector
checks seals, sample order, input bytes, C sentinels, descriptors, traffic and
source/clock/read-depth/UART identities. It requires all 16 cases and at least
30 samples per series. JSON/CSV retain raw counters and min/median/max;
static PNG/PDF figures show useful throughput, transfer volume and tail latency.
`--table-only` needs no plotting packages. `--allow-partial` explicitly lists
missing cases and cannot claim a complete benchmark grid.

## Bounded formal checks

The optional toolchain in [requirements-formal.txt](../requirements-formal.txt)
uses pinned YoWASP Yosys with its built-in SAT solver. It is independent of
the simulation and host environments and needs no proprietary IP.

From PowerShell:

```powershell
python -m venv build/formal-env
.\build\formal-env\Scripts\python.exe -m pip install -r requirements-formal.txt
.\build\formal-env\Scripts\python.exe scripts/formal_dma_rows.py --build-dir build/formal_dma_rows
.\build\formal-env\Scripts\python.exe scripts/formal_buffers.py --build-dir build/formal_buffers
```

With make available, the same runner is exposed as
`make formal PYTHON=... FORMAL_BUILD=build/fresh_rows FORMAL_BUFFERS_BUILD=build/fresh_buffers`. Select fresh directories
for every run; the runner refuses to overwrite evidence. Installation is an
explicit setup step. The runner checks package/binary versions and source
snapshots, then saves actual tool exits, query scripts, logs and witness models.
The [native FIFO/scheduler record](../results/buffer_formal/README.md) and its
[Linux reproduction](../results/buffer_formal/linux/README.md) retain actual
successful exits, source snapshots and decoded witnesses on both platforms.
The [Linux row-planner run](../results/dma_rows/formal/linux/README.md) separately
reproduces its nine queries and seven decoded witnesses.

The [saved DMA row-planner result](../results/dma_rows/formal/README.md)
passes 18 assertions for READ_SLOTS=1/4 through 20 SAT timeframes, including
initial synchronous reset, for a fixed four-row descriptor at a 4 KiB boundary.
It checks stalled offers and DONE, command credits, no DONE with accepted
commands outstanding, and burst metadata. Seven independently decoded
VCD/JSON witnesses demonstrate complete transfers, stalls, four read credits,
simultaneous issue/completion and cancellation with later draining.

This is bounded checking without induction or fairness. It does not prove
arbitrary descriptors, universal successful completion, first-error priority,
FIFO ordering, scheduler ownership or the full accelerator. The result records
the initial-state assumptions and the local queued-read withdrawal exception;
AXI VALID obligations remain a separate contract.

The [FIFO and scheduler record](../results/buffer_formal/README.md) extends
that scope without changing production RTL. The four-entry read metadata FIFO
at T8/T32 passes 37 assertions under one-beat ordered descriptors: an eight-frame
reset-based base and a separate reset-low induction step. The reduced P4/T8
scheduler checks one M1/N9/K1 job in both modes through 32 SAT timeframes,
with 36/33 assertions. Fifteen independently decoded witnesses exercise full
credits, wrap, held payloads, complete jobs, overlap and later fault draining.
The scheduler result is bounded; the FIFO's induction applies only to its
stated model. [formal/README.md](../formal/README.md) lists assumptions and
exclusions. Manual CI dispatch also runs these optional proof checks.

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
This remains the old protocol. The legacy **115200-baud** GEMM board plan is
`make hw-test PORT=COMx LEGACY_MANIFEST=build/gemm/build.json`; it rejects
the current 1 Mbaud image. An explicit command-line `MANIFEST` is also accepted
for existing commands. See the
[DDR GEMM board flow](ddr-board.md). The integrated `make bitstream`,
`make bench` and `make hw-release` interfaces are described above. `make formal`
runs the scoped row-planner, FIFO and reduced-scheduler checks described above.
A passing native UART loopback
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

`make test-tile-dma PYTHON=.venv/bin/python` checks both one/four-read memory integration:
load A/BT from an AXI RAM into the production banks, run the real array, then
store C back through the burst engine. `make test` includes this suite.
`scripts/test_tile_dma.py --only p4_t8|p4_t32|p8_t8|p8_t32` selects one build.
Per-build coverage/XML and the source-hashed summary go to `build/test_tile_dma/`.
See the [adapter contract](tile-dma.md) for its ownership and fault boundaries.
This is a portable AXI simulation, not a MIG or physical DDR GEMM test.

`make test-tile-dma-duplex PYTHON=.venv/bin/python` tests independent load/store
contexts with the concurrent local engine, across P4/P8, T8/T32 and both read
depths. The five named cases check a three-tile pipeline, descriptor snapshots,
independent held completions, BAD_DESC isolation, first-fault capture and
cross-direction draining with held operand, C-read/response and AXI AW/W
offers. Every successful stored matrix is compared in full, including its
padding and guard bytes. Results go to `build/test_tile_dma_duplex/`; `--only`
and `--read-slots` select a configuration, and `--build-dir` preserves a fresh
run. The runner requires exactly the five test names, rejects skipped/failed
tests and checks unchanged source hashes before/after execution. CI runs both
depths in each P/T job. This standalone fixture checks DMA/local-engine
integration; MODE1 packet, vendor and board qualification use the separate
selectable overlap suites above.

`make test-tile-scheduler PYTHON=.venv/bin/python` connects the tagged
[macrotile scheduler](tile-scheduler.md) to the duplex DMA, real local banks
and array. Its three cases check both modes with identical matrix bytes,
row-major addresses and retirement, tails, buffer exclusion, held completions
and fatal drain. Results go to `build/test_tile_scheduler/`; `--only`,
`--read-slots` and `--build-dir` select and preserve runs. The runner requires
all three named tests, rejects failures/skips and checks source hashes before
and after each configuration. Descriptors enter this internal fixture already
validated. UART/register integration, public counters, watchdog and physical
overlap qualification are separate gates.

`make synth-tile` runs the registered-neighbor timing harness for P4/P8,T32
in separate Vivado processes, at 100 MHz with 0.2 ns clock uncertainty. An
explicit BUFG routes the global clock inside the harness. It checks setup
and hold and writes reports to `build/synth_tile/p4` and `p8`.
External harness-pin paths are excluded;
all internal register/BRAM/DSP paths must be timed. The script attempts
[post-route hold fixing](https://docs.amd.com/r/en-US/ug835-vivado-tcl-commands/phys_opt_design)
if needed and fails when negative setup or hold slack remains.
See [tile-engine.md](tile-engine.md) for the interfaces and counter definitions.
The [saved September 30 results](../results/tile_engine/README.md) distinguish
the earlier estimated external-clock failure from the routed-clock checks.
Simulation success does not satisfy the physical timing gate.

## UART-controlled BRAM preview

`make test-preview PYTHON=.venv/bin/python` runs the host unit tests, framed
packet transport, command controller, and complete 8N1 UART integration.
`make test` includes these checks. Generated coverage and source hashes are
under `build/test_packet_transport`, `build/test_preview_controller`, and
`build/test_preview_uart`.

The transport suite rejects malformed frames before command effects and
checks replay/conflict behavior under backpressure. Controller tests include
maximum and non-square matrices, invalid requests, frozen snapshots and
injected internal faults. The UART test drives and samples individual bits,
including baud offset and bad stop bits; it does not bypass the serial PHY.
Host tests enforce the fixed read-only address map independently of the driver.

For a board build and hardware comparison, use native Python with pyserial
and Vivado installed:

```text
python scripts/build_preview.py --baud 115200
python scripts/program_preview.py --manifest build/preview/build.json
python -m host.preview --port COM11 --manifest build/preview/build.json
```

Substitute the actual serial port. Build output stays under `build/preview`.
The programming script verifies the selected bitstream hash and requires one
connected xc7a50t target. It changes volatile configuration RAM, not flash.
The host checks BUILD_ID, geometry, clock and fixed map, loads every required
input, compares every output and saves JSON/CSV. `--repeats 30` repeats each
case with inputs resident in BRAM; every repetition is checked. Core cycle
throughput is reported separately from host-inclusive time.

The [saved September 30 preview run](../results/preview/README.md) passed all
180 board jobs and 90,510 output comparisons at 115200 baud. This records six
cases repeated 30 times, not 180 distinct random matrices or a 30-minute
endurance test. All per-job counters and host timings are retained in JSON/CSV.

See [preview.md](preview.md) for limits, packet/register semantics and the
Vivado checkpoint to inspect. A 1 Mbaud build uses `--baud 1000000`; hardware
validation must be repeated for that bitstream identity.

## AXI transfer primitive

`make test-axi PYTHON=.venv/bin/python` uses cocotbext-axi's RAM model for
ordinary byte-addressed transfers and an independent responder for malformed
responses. It is included in `make test`; no Vivado or DDR model is needed.
Dependencies are pinned after a successful simulator/model smoke test.

The suite exercises all 1..16 beat lengths, all 256 write-strobe masks,
address boundaries, independent AW/W/AR stalls, delayed responses, local
backpressure and held completions. Fault cases cover non-OKAY responses,
incorrect IDs, early/late/missing RLAST, responses before their prerequisites,
faults in the other direction, and new commands coincident with a fault.
The runner removes stale success artifacts and rejects source changes during
the run. The default runner elaborates `READ_SLOTS=1` and `4` separately under
`build/test_axi_burst/read1` and `read4`; the parent summary binds both runs.
`python scripts/test_axi_burst.py --read-slots 4` selects only the queue build.
Queue-specific tests check four ARs before any R, retained slot credit through
DONE, ordered invalid commands, wraparound, simultaneous handshakes and
protocol-fault draining. [Saved primitive evidence](../results/axi/read_queue/README.md)
has a separate source identity from the original serial implementation.
The GEMM build selects `--read-slots 1` (default) or `--read-slots 4`.
External cancellation cases retain held AR/data/DONE, cancel never-offered
reads, keep writes active and require coordinated reset to restore reads.

## AXI MIG vendor simulation

With native Python and Vivado 2026.1 installed:

```text
python scripts/test_mig.py
```

`make sim-vendor PYTHON=python` invokes the same runner where native Python
and Vivado are available. This target is intentionally excluded from portable
CI. The [platform workflow](axi-platform.md) explains the generated project,
configuration checks, calibration/traffic monitor and limits. It verifies the
MIG example and DDR2 model, not the custom burst engine through a bridge or a
physical board. Source hashes, generated-IP hashes and traffic counts are saved
under `build/axi_mig/`; selected evidence is in [results/axi](../results/axi/README.md).

## Integrated DDR diagnostic

`make test-ddr-diag PYTHON=.venv/bin/python` checks the memory-test controller
with the production burst engine, its command backend and the host driver.
It is included in the portable regression.

With native Python and Vivado, run `python scripts/build_ddr_diag.py --stage sim`
and then `python scripts/build_ddr_diag.py --stage bitstream`. The second stage
requires the exact sources and generated platform from the successful first
stage. The Make equivalents are `sim-ddr` and `ddr-diag-bitstream`; both accept
`PYTHON` and `VIVADO` overrides. Output stays under `build/ddr_diag`.

This test connects the custom 64-bit burst engine through SmartConnect to
MIG and its generated DDR2 model. It covers startup and data movement; the
separate warm-reset qualification currently fails the memory-model timing
checks. See the [diagnostic workflow](ddr-diagnostic.md) and
[saved evidence](../results/ddr_platform/README.md) before programming the board.

## Serial DDR job integration

The vendor fixture's reference calculation has a fast standalone regression:

```text
make test-vendor-reference PYTHON=.venv/bin/python
python scripts/test_vendor_reference.py --simulator xsim
```

The first command uses Icarus and runs in portable CI. The second uses native
Vivado tools, with optional `--vivado-bin` for the installation path. Both
extract the actual fixture functions and early self-check, then compare
directed and seeded results plus little-endian byte serialization against
Python integer arithmetic. Neither command instantiates the accelerator or
DDR IP. This catches reference defects before the full vendor simulation;
it cannot replace that integration test.

`make test-ddr-job PYTHON=.venv/bin/python` connects the descriptor/job
controller to the production tile DMA, local engine and AXI burst engine.
It is included in `make test`. The [controller contract](ddr-job.md) describes
the serial checkpoint and its completion/fault boundaries.

Results, coverage and source identities go under `build/test_ddr_job/`.
This remains behavioral AXI RAM simulation; vendor integration and board
GEMM require their own results.

The [serial DDR board flow](ddr-board.md) runs real UART commands through
SmartConnect/MIG and the generated DDR2 model:

```text
python scripts/build_ddr_gemm.py --stage sim
python scripts/build_ddr_gemm.py --stage bitstream
```

The defaults are P4/T32 at 100 MHz, with 115200 baud for the bitstream and
10 Mbaud for the bounded UART-pin simulation. The accelerated setting is not
a physical UART qualification. The second stage requires matching successful
simulation and checks full routed timing/constraints before writing its build
manifest. Outputs are under `build/gemm/`; Make targets are `sim-gemm-ddr` and
`ddr-gemm-bitstream`.

`test-ddr-core` also runs the builder's focused unit tests. Synthetic fixtures
check rejection of stale source/configuration/generated inputs, incomplete
simulation markers and failing physical reports without requiring Vivado.
Programmer tests likewise mock Vivado and ensure altered bitstreams,
simulation/configuration identities or routed reports cannot reach programming.
The [saved build-flow checks](../results/ddr_gemm/README.md) record both native
Windows and Linux results.

`make test-ddr-registers PYTHON=.venv/bin/python` tests the local register map
and job-command handshakes for all four P/T builds, with mocked job status.
It is included in `make test`. Output goes under `build/test_ddr_registers/`;
see the [register contract](ddr-registers.md) for its scope.

`make test-ddr-core PYTHON=.venv/bin/python` tests the host library, then
connects the production packet transport, register bank, job controller,
DMA, banks and compute engine to AXI RAM. Packet commands upload A/BT,
configure a descriptor, start/poll the job and download complete C results.
It also tests host-memory bounds/splits, replay, busy rejection and faults
that retain memory ownership. Output goes under `build/test_ddr_core/`;
Directed cases remove calibration during the first read capture and at the
read/write completion decision, then check empty error replies, stable held
responses, preserved guards and complete draining. A completion-edge fault
must never produce a success response.
The page-boundary sweep checks all 16 aligned starts in the page's final
128 bytes with lengths 8, 120, 128, 136 and 240 bytes. An independent word-address
enumeration checks exact AR/AW burst schedules, covering every first-burst cap
from one to 16 beats. Selected cases hold AR, AW and W for 20 cycles; complete
data, guards, terminal counts and stable channel payloads must still match.
`scripts/test_ddr_core.py --only p4_t8|p4_t32|p8_t8|p8_t32` selects a build.
The [subsystem contract](ddr-core.md) describes this byte-level boundary.
UART pin timing, vendor DDR integration and physical GEMM are separate checks.

`python -m unittest discover -s tb -p test_hw_ddr_gemm.py -v` runs nine
offline board-runner checks. They pin the original P4 workload and matrix
bytes, select P8 explicitly, check literal active-cycle counts, reject
incompatible manifests and verify that CLI admission failures never open COM.
These software checks run in CI and `make test-ddr-core`; the complete
[48-job board plan](ddr-board.md) supplies physical evidence separately.

CI runs compute/interface checks and the four DDR integration geometries in
separate jobs. The maximum-dimension tests retain complete output, guard and
transaction checks; they are not replaced by a checksum to reduce runtime.

## Physical tiling and overlap comparison

The [completed 1 Mbaud comparison](../results/ddr_overlap/release_1mbaud/comparison/README.md)
uses 16 shapes and 30 samples per configuration. Run each command only with
its matching qualified image already programmed after a separate cold start;
see [the board procedure](ddr-board.md). These commands do not program or reset
the FPGA. Choose fresh output directories and keep the laptop awake.

```text
python scripts/keep_awake.py -- python scripts/qualify_release.py --port COM11 --manifest build/gemm_release_p8_t8_1mbaud/build.json --phase benchmark --modes 0 --samples 30 --seed 20261004 --oracle numpy --output build/reproduce_t8_grid
python scripts/keep_awake.py -- python scripts/qualify_release.py --port COM11 --manifest build/gemm_release_p8_t32_1mbaud/build.json --phase benchmark --modes both --samples 30 --seed 20261004 --oracle numpy --output build/reproduce_t32_grid
python scripts/collect_benchmarks.py --t8 build/reproduce_t8_grid --t32 build/reproduce_t32_grid --output build/reproduce_comparison
```

Install the pinned [benchmark dependencies](../requirements-benchmark.txt)
for the collector and [host dependencies](../requirements-host.txt) for board
access. These comparison commands select preserved local image archives.
A clean clone must regenerate matching P8/T8 and P8/T32 builds in separate
directories, with the same source, clock, read-depth and UART settings, then
substitute their manifests. `make bitstream RELEASE_T=8 RELEASE_BUILD=build/gemm_t8`
builds the T8 baseline; the default current build is T32. Each image requires
its own cold-start programming before measurement.
The runner checks every output and complete guarded allocations before
sealing a case; the collector requires matching source, input and layout
identities. Error bars show minimum/maximum around each series median.
The public archives retain standalone software-only validators and exact
method snapshots. Their grid replay checks metadata and counters; it cannot
reproduce numerical comparisons without per-job raw matrices.

## DMA row sequencing

`make test-dma PYTHON=.venv/bin/python` tests both read depths of the row sequencer's
addresses, burst metadata, byte strobes and completion handshakes. It is
included in `make test`. The independent scoreboard enumerates row word
addresses, then checks that issued bursts cover them exactly once without
crossing a row, 16 beats or a 4 KiB boundary. Request snapshotting, held
payloads, delayed/error completions and invalid descriptors are tested too.

Output is stored under `build/test_dma_rows/`. This portable test does not
connect operand/result banks, the AXI engine or the vendor DDR platform.
See the [row sequencer contract](dma-rows.md) for that integration boundary.
