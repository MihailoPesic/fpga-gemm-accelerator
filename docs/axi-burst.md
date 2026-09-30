# AXI burst primitive

`gemm_axi_burst` moves one bounded burst at a time in each direction. A read
and a write may run concurrently. It is a reusable memory-transfer primitive,
not the matrix tile scheduler or a complete DDR DMA controller.

## Local contract

The module runs on one clock, with synchronous active-high `rst`. Read and
write commands use independent valid/ready interfaces:

```
rd_cmd_{valid,ready,addr[31:0],beats[4:0],tag[TAG_W-1:0]}
wr_cmd_{valid,ready,addr[31:0],beats[4:0],tag[TAG_W-1:0]}

wr_data_{valid,ready,strb[7:0]} + wr_data[63:0]
rd_data_{valid,ready,index[3:0],last,tag[TAG_W-1:0]} + rd_data[63:0]

rd_done_{valid,ready,status[15:0],tag[TAG_W-1:0]}
wr_done_{valid,ready,status[15:0],tag[TAG_W-1:0]}
```

`TAG_W` defaults to 16. Tags are opaque caller metadata. Addresses are bytes;
`beats` is the actual count, 1 through 16, not AXI LEN. Commands are captured
only on valid/ready. A direction accepts no further command until its held
completion is consumed. The other direction is independent. The caller holds
command and write-data payloads stable while VALID is stalled; a rejected
write completion releases any still-pending local data for that command.

Commands must be 8-byte aligned, remain within one 4 KiB page and address only
bytes representable by 32 bits. Invalid commands return status 3 (BAD_DESC)
without asserting AXI VALID. The final aligned beat at `0xfffffff8` is legal;
its last byte is `0xffffffff`. DDR-window, allocation and matrix-row checks
belong to the caller. The caller splits at row ends, page boundaries and
16 beats; the primitive does not split a rejected command.

An accepted valid write takes exactly `beats` local data/strobe handshakes.
Every strobe mask is supported, including zero. No AXI AWVALID or WVALID is
asserted until the entire burst has been buffered. AXI WLAST is generated
from the stored count; the local data interface has no LAST signal. Completion
requires accepted AW, all W beats and the B response. Output-buffer ownership
must survive through that completion.

Read storage reserves a full 16-beat buffer before AR. All response beats are
checked before any local data is published. A successful burst then streams
its words with ascending indices, the command tag and LAST on the final word.
Read completion appears after the last local data handshake. Failed reads
publish no data. Local data and completions hold VALID and payload until READY.
RAM contents are not reset; state and ownership are reset.

## AXI boundary

The master has 32-bit addresses, 64-bit data, eight WSTRB bits and one-bit ID
ports tied to ID zero. It emits INCR bursts, SIZE=3 and LEN=beats-1. LOCK,
CACHE, PROT, QOS and REGION are zero. A vendor bridge supplies any required
width/clock conversion to the generated MIG interface.

AW and W are independent: either may be accepted first, and neither VALID
waits for its READY. Once asserted, AR/AW/W VALID and payload survive stalls,
including a simultaneous fault in the other direction. RREADY and BREADY
remain asserted outside reset; read capacity is already reserved. Unexpected
response beats are consumed and reported as protocol faults.

RVALID must follow an AR handshake on an earlier edge. BVALID must follow
both the AW handshake and the final W handshake on earlier edges. A response
on the same edge as its prerequisite handshake is premature. Such a response
does not retire the pending transaction: AR/AW/W obligations continue, and a
later legally timed response is required before completion or quiescence.

These dependencies follow the [Arm AXI specification](https://developer.arm.com/-/media/Arm%20Developer%20Community/PDF/IHI0022H_amba_axi_protocol_spec.pdf)
and the checks summarized by [AMD's AXI Protocol Checker](https://docs.amd.com/r/en-US/pg101-axi-protocol-checker/AXI-Protocol-Checks-and-Descriptions).

## Errors and reset

Status 0 means that command succeeded. A non-OKAY RRESP or BRESP is status 7
(MEM_RESP). Incorrect response ID, RLAST/count, unexpected response or premature
R/B is status 8 (PROTOCOL). These faults latch `fatal` and `fatal_code` and block
new commands. The first fatal code is retained; simultaneous protocol and
response errors prioritize PROTOCOL. A command retains its first own error.

Already issued AXI transactions retain their obligations. After an accepted
AR, early RLAST closes the malformed response with an error. A response with
an incorrect ID also retires the malformed response (at RLAST for reads or B
for writes), provided it follows the required address/data handshakes. If the
expected final beat lacks RLAST,
the engine discards further beats without overrunning storage and drains until
a later RLAST. A missing terminal response can prevent completion indefinitely;
the system watchdog and coordinated reset are responsibilities of the platform.

A fault while a write is still collecting local data cancels that unissued
command with status 8. No AW/W is emitted, and the completion releases any
pending local write-data offer. A new command accepted on the same edge as a
fault is likewise rejected with status 8 and no AXI effects. An address VALID
already asserted before that edge remains an obligation even if it has not
handshaken.

Per-command status describes its own issued transaction: a correct concurrent
burst can finish with status 0 after the other direction faults. Previously
published data/completion payloads are never rewritten. The enclosing job must
also observe `fatal` and cannot report successful job completion after a fault.

`axi_quiescent` means no address, data or response obligations remain at this
master boundary; local buffered data/completions may still be pending.
`progress` pulses on accepted AXI transfers, local write data or local read
delivery. Neither signal clears fatal state. Reset must include the master,
conversion and memory subsystem as a coordinated platform action; resetting
only this master during outstanding AXI work is not supported.

The primitive has no watchdog, calibration input, descriptor validation,
matrix address generator, overlap scheduler or four-read concurrency. Those
remain separate integration work. Simulation-only assertions check stable
master/local payloads under stalls, full-write buffering and buffer bounds;
they are not a formal proof.
