# Operand memory and prefetch

`gemm_bram_microtile` wraps the existing compute core with two sets of banked
A/BT storage. Supported builds are P=4/8 and T=8/32. K remains 1..256.
This block computes one selected microtile. The [local matrix engine](tile-engine.md)
adds result memory and iteration over the required microtiles. The
[BRAM preview](preview.md) already connects that engine to UART commands and
a working board top. The [tile DMA adapter](tile-dma.md) connects the operand
and result ports to AXI bursts. The [serial DDR controller](ddr-job.md)
validates descriptors and sequences larger matrix jobs; the
[packet subsystem](ddr-core.md) and [board wrapper](ddr-board.md) connect this
path to UART and the [board platform](axi-platform.md). The current selectable
DDR build integrates these banks under the [tagged scheduler](tile-scheduler.md)
and [overlap job controller](ddr-overlap.md). Its image and qualification
scope are recorded in [status](status.md); earlier physical baselines retain
their separate evidence.

```
64-bit load port -> A banks  ----> current/next words --+
                 -> BT banks ----> current/next words -+-> gemm_microtile -> drain
                          synchronous reads           |   (compute core)
```

## Loading and ownership

A load transfers on a rising edge with `load_valid && load_ready`.
`load_bt` selects A (0) or BT (1); `load_buf` selects buffer 0/1; `load_q`
selects a local row of A or BT, and `load_word` selects eight consecutive
reduction elements. Byte 0 occupies bits 7:0. Entire 64-bit words are written.
The producer must hold a pending valid request and its payload until accepted.

The physical map is:

```
bank = q % P
word = buf * (T/P) * 32 + (q/P) * 32 + word_index
```

Each of the P A and P BT banks has an independent synchronous read port.
Banking supplies one operand for each array lane simultaneously: P adjacent
rows have different `q % P` values. A 64-bit write deposits eight consecutive
k values at once, while compute extracts byte `k % 8`. Transposing B makes
each output column's reduction elements contiguous like an A row. Keeping
the full K dimension locally avoids stopping the array for DDR responses.

No BRAM contents are cleared on reset. The caller must load all useful words
before starting and maintain buffer-ready ownership; this block does not track
which addresses have been initialized. Invalid rows and columns are masked.

An accepted start snapshots buffer, groups, K and valid row/column counts.
`a_group` and `bt_group` select P consecutive local rows of A/BT, respectively.
Valid rows/columns are independently 1..P. Illegal or busy starts pulse
`cmd_error` without changing an active operation. A same-edge start takes
priority over a load to the buffer being acquired.

Loads to the active buffer are blocked from acceptance through final drain.
Loads to the other buffer can proceed throughout compute. At completion the
input buffer becomes writable again; result ownership belongs to the caller.
This is the wrapper's capability; whole-system overlap also needs ownership
at the local-engine and scheduler boundaries. The default
`gemm_tile_engine` configuration (`CONCURRENT_PORTS=0`) blocks public
load/read ports during a job. The BRAM preview and serial DDR path use that
configuration. The current selectable DDR build sets `CONCURRENT_PORTS=1`;
its tagged scheduler owns separate input and result sets while load, compute
and store overlap. See [local-engine ownership](tile-engine.md) and
[overlap integration](ddr-overlap.md).

Reset cancels the local operation and suppresses loads, feed and drain. This
local reset contract is not an AXI transaction-abort mechanism.

## Synchronous read schedule

Edges are relative to the wrapper's accepted start:

```
edge              operation
0                 snapshot command, acquire buffer
1                 BRAM reads word 0
2                 capture current=word 0; BRAM reads word 1 if K>8
3                 capture next=word 1; launch existing compute core
4                 core consumes k=0
3+K+2P            capture first result row
K+3P+2            capture final result row; done pulses, busy falls
```

While consuming byte 6 of word n, request word n+2 if it exists. At byte 7,
the response replaces `next` while the old `next` becomes `current`.
The BRAM response therefore arrives before the next eight-element boundary.
There is no external memory stall in an active microtile.

The wrapper adds three edges to the existing core schedule. Earliest accepted
wrapper starts are K+3P+3 clocks apart: 283 clocks for P8,K256. This excludes
input loading and all downstream result-storage scheduling; it is not a job
throughput measurement.

## Verification

`make test-memory` runs all four P/T builds. An independent Python dot-product
oracle checks results while visiting every input bank word, both buffers and
every A/BT group pairing. Tests cover K boundaries (including 8/16 transitions
and 255/256), every tail class, command snapshotting, blocked same-buffer
loads, concurrent opposite-buffer loads, invalid/busy starts, reset in each
prefetch phase and during computation/drain, and 50 seeded random jobs per build.

`make synth-memory` checks BRAM inference and P squared DSP usage with Vivado.
It is synthesis only; it does not establish timing closure for this wrapper
or for a future board design. Logs are generated under `build/synth_memory/`.
Saved [September 30 results](../results/operand_memory/README.md) contain
the source hashes, coverage and synthesis reports.
