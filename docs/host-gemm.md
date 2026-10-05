# DDR GEMM host library

[`host.gemm`](../host/gemm/__init__.py) implements the Python interface to the
[DDR subsystem](ddr-core.md). It reuses the COBS/CRC stop-and-wait link
from the preview. ID `0x314d474e` identifies the GEMM interface. Version
`0x00000100` supports serial jobs; development version `0x00000200` supports
selectable serial/overlap jobs. Both remain separate from the full v1 target.

The library and CLI run on the qualified P4/P8 serial images and current
[1 Mbaud selectable P8 image](../results/ddr_overlap/release_1mbaud/board/t32/dense/README.md)
`0x9d4beb4d`. Thirty matched pairs measure overlap at 11.298 useful GOPS for
256x256x256. Earlier images retain their original identities. The preview
and diagnostic bitstreams use different interfaces.
The [board integration workflow](ddr-board.md) supplies the UART/platform
wrapper, manifest-gated build and cold-start programming procedure.

## Packing and addresses

`pack_inputs(A,B)` accepts mathematical A[M][K] and B[K][N]. Elements must be
signed INT8. It returns immutable mathematical inputs, packed A/BT rows and a
validated descriptor. Python integer arithmetic supplies the independent
oracle; signed INT32 results are decoded little-endian.

```text
Stored A row i:  A[i,0], A[i,1], ..., A[i,K-1], padding
Stored BT row j: B[0,j], B[1,j], ..., B[K-1,j], padding

A_STRIDE  = round_up(K, 64)
BT_STRIDE = round_up(K, 64)
C_STRIDE  = round_up(4*N, 64)
```

The default layout allocates disjoint 64-byte-aligned regions, with space for
optional guards. Descriptor validation checks complete conservative
allocations against the 128 MiB window with Python's widened integers.
Nonzero input padding deliberately exercises the hardware K mask. Guarded
upload also initializes C and surrounding bytes; readback checks all inputs,
C row padding and guards separately from every useful output.

Memory APIs accept aligned complete eight-byte words. They split transfers
into at most 240-byte packets and at 4 KiB boundaries. The backend independently
validates each packet and splits AXI bursts to at most 16 beats.

## Job lifecycle

```text
identify -> wait_ready -> upload -> configure -> start -> wait -> read_output
                                                           |          |
                                                     counters      validate
```

`identify()` is read-only. It checks PING identity, geometry, BUILD_ID and
CORE_HZ, and begins a fresh software session. `load_manifest(path)` checks
the selected local bitstream SHA-256. Passing that manifest to `identify()`
compares the hardware's reported geometry, BUILD_ID and clock with its fields.
UART provides BUILD_ID; it does not read back a bitstream hash from the FPGA.

`pack_inputs(A,B,mode=1)` selects overlap on a matching development image;
mode defaults to zero. Upload and configuration reject MODE=1 on an identified
serial image before any register or memory writes. PING, register VERSION and
any manifest ID/version must agree. See [overlap integration](ddr-overlap.md).

`configure()` writes the descriptor while idle. `start(job_id)` checks its
register readback before writing JOB_ID and START. START's response means
validated acceptance, not completion. `wait(job_id)` checks status/errors and
LAST_JOB_ID, then reads the frozen counters before any later START.
`read_output()` requires that matching completed job and compares no checksum
in place of actual values. For an odd N, the last read includes row padding;
only N signed INT32 values are returned.

Reset/reconnect invalidates input residency, descriptor and completion state.
Re-identify and reload inputs. The link retries only byte-identical packets
within its current session. An uncertain transport outcome poisons that link;
a timeout cannot authorize a new START or an abort of outstanding AXI work.
Recoverable status clearing is explicit. Fatal errors require coordinated
platform reset.

## Measurements and CLI

`benchmark()` uploads once, then runs resident jobs. It fully downloads and
validates each output and optionally checks guards. It also checks independent
read/write volume and compute-cycle formulas for either schedule.

```text
Useful GOPS = 2*M*N*K*CORE_HZ / JOB_CYCLES / 1e9
Utilization = M*N*K / (P*P*JOB_CYCLES)
```

JOB_CYCLES includes DDR tile transfers and the final successful result B
handshake. Packing, upload, configuration, host command/poll/counter time,
download, validation and optional guard checks have separate host timings.
Later repetitions disclose reused DDR inputs. The host-inclusive first run
includes packing/upload/configuration and output movement; validation and
extra guard read/check time are reported separately.

`python -m host.gemm --help` shows the deterministic demo options. With a
qualified matching bitstream loaded, select its manifest and UART port.

```text
python -m host.gemm --port COM11 --manifest build/gemm_release_p8_t32_1mbaud/build.json --mode 1 --m 5 --n 3 --k 9 --repeats 3 --retries 0 --output build/my_gemm_demo
```

This existing-image command reloads inputs and checks every result, input
allocation, output padding and guard region. It prints PASS and measured
cycles for each job. Choose a fresh output directory for each saved demo;
the CLI overwrites JSON/CSV in the selected directory. The local build archive
must be present, and the board must report its matching P8 identity.

The CLI saves JSON/CSV under ignored `build/gemm/results/`, including seed,
identity, source hashes, manifest, raw counters, timings and failures.
It returns nonzero on mismatches, timeouts or hardware errors. Without a
manifest it checks UART identity but cannot verify a selected bitstream file.

The host unit tests run without a board or pyserial. Actual hardware access
requires [requirements-host.txt](../requirements-host.txt).

## Use your own matrices

The CLI generates deterministic matrices for demonstration and benchmarking.
The Python API accepts your own signed INT8 matrices and returns C as a list
of rows of signed INT32 integers. With the qualified image programmed and
its matching local archive present:

```python
from host.gemm import GEMM, load_manifest, pack_inputs, validate

A = [[1, -2, 3], [4, 5, -6]]       # 2 x 3
B = [[7, 8], [-9, 10], [11, -12]]  # 3 x 2
manifest = load_manifest("build/gemm_release_p8_t32_1mbaud/build.json")
device = GEMM.open("COM11", baud=manifest["baud"])
try:
    device.identify(manifest)
    device.wait_ready()
    packed = pack_inputs(A, B, mode=1)
    device.upload(packed, guards=True)
    device.configure(packed.descriptor)
    device.start(job_id=1)          # acknowledgement means accepted
    counters = device.wait(job_id=1)
    C = device.read_output()       # [[58, -48], [-83, 154]]
    validate(C, A, B)
    device.check_guards(packed)
    print(C, counters["job_cycles"])
finally:
    device.close()
```

Pass `mode=0` to select serial scheduling on this same image. Resident matrices
can be rerun by submitting a new job ID, with no intervening upload.

This example was [checked on image `0x2c680af7`](../results/ddr_overlap/timing_predicate/final_build/host_api/record.json)
in MODE=1: all four results matched, 752 input/padding/guard bytes were intact,
zero transport retries, and JOB_CYCLES=264. The [earlier serial check](../results/ddr_overlap/serial_host_smoke/custom_matrix/README.md)
retains its separate image and measurements.

The host packs A and transposes B into BT, uploads them through memory
commands, writes the descriptor and sends START. The FPGA works from DDR2
without further host involvement. `wait()` polls status; DONE is visible
only after every C write is acknowledged. Reading C is a separate command,
so the FPGA does not automatically stream the whole result on completion.
Inputs can stay in DDR2 for subsequent jobs during the same powered session.

USB-UART limits movement between laptop and board. The current qualified
image uses 1 Mbaud; earlier images use 115200 baud. Even at 1 Mbaud, host
transfers can take much longer than FPGA compute. This project measures
memory architecture and FPGA execution with on-board counters; it does not
claim a USB end-to-end speedup over a laptop matrix library.
