# BRAM preview interface

This build connects the P4/T32 local engine to USB-UART. It computes signed
INT8 `C = A * B`, with INT32 outputs, for `1 <= M,N <= 32` and
`1 <= K <= 256`. The host explicitly transposes B before upload. One job is
active at a time. All operands needed by a job must be loaded before START.

```text
Python -> USB-UART -> COBS/CRC + retry cache -> command controller
                                                   |
                     A / BT banks -> P4 array -> C banks
                                                   |
Python <- USB-UART <- results and frozen cycle counters
```

The preview uses local addresses, fixed strides, and buffer zero. It has no
DDR controller, DMA, overlap, watchdog, or DDR completion semantics. It is a
separate build of the same repository and shared compute RTL. The original
native-DDR project remains available for platform work.

## Packets

The [target transport](specification.md#host-transport-and-software-contract)
is implemented: COBS with a zero delimiter; decoded header `version:u8=1,
opcode:u8, sequence:u16, payload_length:u16`, then payload and reflected IEEE
CRC32. All fields are little-endian. CRC covers header and payload. Complete
validation precedes any command effect; corrupt frames are discarded.

| Opcode | Request payload | Successful response data |
| --- | --- | --- |
| 0x01 PING | nonce:u32 | nonce, ID, VERSION, all u32 |
| 0x02 READ_REG | address:u16 | value:u32 |
| 0x03 WRITE_REG | address:u16, value:u32 | Empty |
| 0x04 MEM_READ | address:u32, length:u16 | Requested bytes |
| 0x05 MEM_WRITE | address:u32, length:u16, data | Empty |

Responses use opcode OR 0x80 and echo the sequence; their payload begins with
status:u16. Statuses are 0 OK, 1 BAD_CMD, 2 BAD_ADDR, 3 BAD_DESC, 4 BUSY,
5 NOT_READY, 6 SEQ_CONFLICT, and 8 PROTOCOL for an unexpected engine failure.
The controller validates command lengths and entire memory ranges before
issuing any local-memory operation.

The host uses stop-and-wait. An exact duplicate of the last completed request
replays its response without repeating effects. Reusing its sequence with
different bytes returns SEQ_CONFLICT and retains the previous cache. Reset
invalidates the cache and all host assumptions about loaded matrices. A
bounded receiver can retain one incoming frame while a command/reply is
active; extra pipelined traffic is discarded through its delimiter. This is
not a queued multi-command interface.

## Local memory

| Region | Byte range | Row stride | Access |
| --- | --- | --- | --- |
| A | 0x0000..0x1fff | 256 | Write |
| BT | 0x2000..0x3fff | 256 | Write |
| C | 0x4000..0x4fff | 128 | Read after completion |

Memory requests must be 8-byte aligned, contain 8..240 bytes in multiples of
8, and remain inside one row. Upload `round_up(K,8)` bytes per valid operand
row; bytes beyond K are ignored by compute. Read only valid completed C rows
and `round_up(4*N,8)` bytes per row. An odd final INT32 has a zero upper word
in its last 64-bit read. C row padding cannot be read through this interface.
All memory commands return BUSY during compute.

## Registers

Aligned full-word accesses only. Unknown addresses return BAD_ADDR; writes
to read-only registers return BAD_CMD. Configuration is writable only when
idle. Dimensions retain all 32 written bits and are validated on START.

| Address | Register | Behavior |
| --- | --- | --- |
| 0x00 | ID | 0x3142474e; bytes `NGB1` |
| 0x04 | VERSION | 0x00000100; BRAM preview |
| 0x08 | GEOMETRY | 0x01002004; KMAX=256, T=32, P=4 |
| 0x0c | STATUS | READY bit0, BUSY bit1, DONE bit2, ERROR bit3, RESET_REQUIRED bit5; other bits zero |
| 0x10 | CONTROL | Write 1 START or 2 CLEAR_STATUS; reads zero |
| 0x14 | JOB_ID | Software job identifier |
| 0x18 / 0x1c / 0x20 | M / N / K | Dimensions |
| 0x24 / 0x28 / 0x2c | A_BASE / BT_BASE / C_BASE | Read-only 0 / 0x2000 / 0x4000 |
| 0x30 / 0x34 / 0x38 | A_STRIDE / BT_STRIDE / C_STRIDE | Read-only 256 / 256 / 128 |
| 0x3c | MODE | Only zero is supported |
| 0x40 / 0x44 | ERROR_CODE / LAST_JOB_ID | Job status |
| 0x48 | CORE_HZ | 100000000 |
| 0x50 | BUILD_ID | Source/configuration identity from build manifest |
| 0x80 / 0x84 | JOB_CYCLES | Low / high u32 of completed-job snapshot |
| 0x88 / 0x8c | COMPUTE_CYCLES | Low / high u32 of completed-job snapshot |

START snapshots the dimensions and job ID, clears DONE/recoverable error,
and acknowledges acceptance. DONE follows the final local C write. Invalid
dimensions latch BAD_DESC without a start. CLEAR_STATUS is idle-only.
Ordinary command errors do not destroy the active job's status. Previous
counter snapshots remain readable until the next completed job replaces them.
The full-release DDR_READY bit is always zero.
An unexpected internal PROTOCOL fault latches RESET_REQUIRED, suppresses
successful completion and result access, and blocks further mutations until
the board is reset. Diagnostic reads and PING remain available. CLEAR_STATUS
cannot clear this fault. This is a local-engine reset rule; DDR transaction
draining is outside the preview.

For `tiles = ceil(M/4) * ceil(N/4)`, the implemented local schedule predicts:

```text
job_cycles     = tiles * (K + 15)
compute_cycles = tiles * (K + 11)
useful_GOPS    = 2*M*N*K * core_hz / job_cycles / 1e9
```

JOB_CYCLES measures engine START acceptance through the final local C write.
These counters exclude UART validation/dispatch, transfers and host work. Host measurements must
report packing, upload, job wait, download and validation separately.

## Build and inspect

```text
python scripts/build_preview.py --baud 115200
```

The build needs Vivado with Artix-7 support, but no generated MIG IP or board
file download. It writes `build/preview/gemm_preview.bit`, `build.json`, timing,
utilization, CDC and methodology reports, and `preview_routed.dcp`. A manifest
is emitted only after the timing and critical-warning gates pass and the
sources are checked again for changes during the build.

In Vivado's Tcl Console, open the routed design with:

```tcl
open_checkpoint {C:/path/to/nexys-accelerator/build/preview/preview_routed.dcp}
```

Use Reports -> Timing and Reports -> Utilization to inspect it. Program the
new `gemm_preview.bit` using Hardware Manager on the detected xc7a50t. LED0
means reset released, LED1 busy, LED2 completed, LED3 job error. The CPU_RESET
button invalidates the job, protocol cache and matrix-valid state. This build
does not use the native-DDR baseline's calibration LEDs.

The host runner verifies the manifest's bitstream file hash and compares the
hardware BUILD_ID/geometry/clock. BUILD_ID is a 32-bit source/configuration
identifier, not a cryptographic measurement of FPGA configuration memory.
