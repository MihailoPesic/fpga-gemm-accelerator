# DDR register interface

[`gemm_ddr_registers`](../rtl/control/gemm_ddr_registers.sv) exposes the
descriptor and status registers for the [serial job controller](ddr-job.md).
It uses a local request/response bus, not AXI-Lite. Both modules share the
core clock. The [packet subsystem](ddr-core.md) connects packet decoding and
idle host-memory access at their separate interfaces.

## Local bus

Requests carry a 16-bit byte address, read/write selection, 32-bit write data
and four write strobes. Responses carry 32-bit read data and 16-bit status.
Each side uses its own valid/ready handshake. One request is outstanding;
fields are captured at acceptance and a response holds until consumed.

Every access must be word aligned. Writes must enable all four bytes.
Unaligned/unknown addresses return BAD_ADDR (2); partial writes and writes
to read-only fields return BAD_CMD (1), with no register or job effects.
Configuration writes while a job is busy return BUSY (4).

The register map follows the [specification](specification.md), with the
read-only BUILD_ID extension at 0x50. ID, VERSION, build identity and clock
are parameters; geometry reports P, T and KMAX=256. Reset clears descriptor
fields and sets WATCHDOG_LIMIT to 10,000,000 cycles.
WATCHDOG_LIMIT=0 is rejected at write time. MODE is stored unchanged, so
the serial controller can reject unsupported modes at START validation.
The portable register block alone does not advertise a released bitstream
or compliance with the complete DDR/overlap system.

## START and status clearing

```text
CONTROL=1 request accepted
        |
        v
Held job START -> descriptor validation -> job response
                                            |
                                            v
                              Held local register response
```

CONTROL=1 acknowledges the controller's validated acceptance or error.
It cannot return success merely because the register write was received.
Once accepted, the job proceeds independently of response backpressure.
The register block captures BUSY when the local request is accepted. A busy
START, configuration write or CLEAR is rejected without effects, even if the
old job finishes during decoding. An initially idle START is forwarded;
the job controller can still reject it if admission changes before acceptance.
Configuration remains stable through the pending command because no second
register request is admitted.

CONTROL=2 produces one CLEAR_STATUS pulse and returns the controller's status
code. Other CONTROL values return BAD_CMD. Clearing does not remove a fatal
reset requirement. CONTROL reads as zero; it is an action, not stored state.

## Counter reads

Eight 64-bit counters occupy low/high words at 0x80..0xbc. The job controller
owns their frozen snapshots; the register block does not duplicate them.
Ordinary active-job reads return BUSY. After a fatal fault, reads are allowed
even while issued traffic is still draining or hung, because that first-fault
snapshot is frozen.

Software polls STATUS, checks LAST_JOB_ID and errors, then reads the completed
snapshot before submitting another START. The bus does not lock a snapshot
across multiple register reads. Status bits expose READY, BUSY, DONE, ERROR,
DDR_READY and RESET_REQUIRED; unused bits are zero. Configuration reads report
the stored descriptor, while LAST_JOB_ID reports the most recently accepted job.

## Verification boundary

`make test-ddr-registers PYTHON=.venv/bin/python` checks all P/T builds with
independent register requests and mocked job command/status ports. It tests
map values, invalid writes, busy access, command propagation, reset and held
responses. This is a bus/command unit test. Complete matrix behavior is tested
separately by `make test-ddr-job`. `make test-ddr-core` adds real packet commands
and host-memory arbitration against AXI RAM. The
[board integration](ddr-board.md) has separate vendor, routed and physical
evidence for the original serial checkpoint. Those gates must be repeated
for the changed C-gather adapter before extending its hardware claim.
The [saved unit result](../results/ddr_registers/README.md) passes 16 tests
with unchanged source fingerprints.
