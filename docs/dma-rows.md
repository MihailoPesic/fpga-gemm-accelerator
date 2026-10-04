# Row burst sequencer

`rtl/memory/gemm_dma_rows.sv` plans one read or write operation of up to 32 rows,
with up to 256 useful bytes per row. It splits rows into aligned 64-bit bursts
of 1..16 beats without crossing a 4 KiB boundary. It carries no data and does
not drive AXI. A bank adapter and the existing burst engine must execute its
commands; this module alone is not a complete tile DMA.

## Request and validation

All ports use the same clock. `rst` is synchronous and active high. A request
handshake (`req_valid && req_ready`) snapshots these fields:

| Field | Width | Meaning |
| --- | ---: | --- |
| `req_write` | 1 | 0 reads, 1 writes |
| `req_base` | 32 | First row's byte address; multiple of 8 |
| `req_stride` | 32 | Bytes between rows; multiple of 8 |
| `req_rows` | 6 | 1..32 rows |
| `req_row_bytes` | 9 | 1..256 useful bytes per row; writes require a multiple of 4 |

Validation uses three edges after acceptance, before offering any burst:

```text
rounded_bytes = round_up(req_row_bytes, 8)
req_stride >= rounded_bytes
touched_end = req_base + (req_rows - 1) * req_stride + rounded_bytes
touched_end <= DDR_BYTES
```

The end calculation uses 39 bits, including invalid input combinations, so it
cannot pass by wrapping a 32-bit address. `DDR_BYTES` is a 33-bit parameter,
default 134217728; supported values are 1..4294967296. The exclusive end may
equal the window limit. Invalid requests return status 3 with no burst issued.
Only actual rounded transfers are checked here. The higher descriptor layer
still must validate 64-byte base alignment, matrix dimensions, full conservative
allocations, and region disjointness.

The request edge registers the rounded row length and last-row index. The next
edge registers the 38-bit stride product and a separate 33-bit base-plus-length
sum; the following edge combines them into the 39-bit exclusive endpoint.
Validation then checks that registered endpoint. These stages keep the
multiplier, endpoint addition and bounds-controlled burst enables out of a
single combinational path.

## Burst and completion ports

`burst_valid` offers a command until `burst_ready`. Its entire payload stays
stable while stalled. In four-read mode, a fatal cancellation may withdraw an
unaccepted local command; this is not an AXI address interface:

| Field | Width | Meaning |
| --- | ---: | --- |
| `burst_write` | 1 | Snapshotted transfer direction |
| `burst_addr` | 32 | Byte address of the first beat |
| `burst_beats` | 5 | Actual beat count, 1..16 |
| `burst_row` | 5 | Local row index, 0..31 |
| `burst_word` | 5 | First 64-bit word index within the row, 0..31 |
| `burst_row_last` | 1 | This burst ends its row |
| `burst_last` | 1 | This burst ends the entire operation |
| `burst_final_strb` | 8 | Byte mask for this burst's last beat |

All earlier beats have mask `0xff`. Reads always report `0xff`. Writes report
`0x0f` only for a final row beat containing one useful INT32 value; every other
write mask is `0xff`. Read padding is fetched and must be ignored by the consumer.
The [tile DMA adapter](tile-dma.md) uses the row/word metadata to deliver or
collect the burst through the existing local-engine ports.

`READ_SLOTS=1` is the default: exactly one burst is outstanding. After its command handshake,
`complete_ready` waits for `complete_valid` and `complete_status[15:0]`. The
client must not offer completion before that command has been accepted. A read
acknowledgement means all returned data reached its destination; a write
acknowledgement includes its AXI B response. Status 0 advances the planner;
any nonzero status stops further commands and is returned unchanged.

`READ_SLOTS=4` permits up to four accepted read commands. The issue cursor
advances on each command handshake; an independent count retires ordered
completions. The client must reserve command metadata before acceptance and
complete reads in command order. Writes retain the one-burst schedule in both
builds. No credit is freed by AXI RLAST alone: completion includes the bank
delivery and local terminal handshake.

For queued reads, `cancel/cancel_status` stop further offers, retain the first
nonzero error and wait for every already accepted command to retire. With no
accepted commands, cancellation can finish immediately. A cancel with zero
status reports PROTOCOL (8). The client still owns AXI obligations.

`done_valid` holds `done_status[15:0]` until `done_ready`. `busy` covers request
acceptance through that final handshake, including validation and completion
backpressure. `req_ready` is asserted only while idle. There are no tags because
completions are strictly ordered. The caller must retain buffer ownership
until the operation completes.

## Examples and edge timing

```text
base=0x0ff8, row_bytes=256, rows=1, stride=256
  address  beats  row  word  row_last  last  final_strb
  0x0ff8      1    0     0      0       0       ff
  0x1000     16    0     1      0       0       ff
  0x1080     15    0    17      1       1       ff

write: base=0x2000, row_bytes=124 (31 INT32s), rows=2, stride=128
  0x2000     16    0     0      1       0       0f
  0x2080     16    1     0      1       1       0f
```

With `READ_SLOTS=1` and ready inputs high, a one-burst successful operation has this minimum
schedule. Values in the last column apply after that edge:

```text
edge  event                         state/output afterward
0     request accepted              busy=1; saved fields stable
1     product/base sum registered   no burst or completion offered
2     footprint registered          no burst or completion offered
3     validation succeeds           burst_valid=1
4     burst command accepted        complete_ready=1; no next burst
5+    downstream completion taken   done_valid=1, done_status=0
6+    result accepted               busy=0, req_ready=1
```

An invalid request instead raises `done_valid/status=3` after edge 3. A
successful nonfinal acknowledgement offers the next burst after that edge;
it cannot be accepted on the same edge as the preceding acknowledgement.

Reset clears only this module's control state. During integration it must be
coordinated with the complete memory system, because resetting this planner
alone cannot cancel an AXI obligation. Abort, watchdog, calibration monitoring,
buffer ownership and data movement belong to the surrounding subsystem.

## Verification

The [focused test suite](../tb/test_dma_rows.py) passes 10 tests across read
depths one and four, including Icarus `-Wall` compilation. Run it with
`python scripts/test_dma_rows.py`; the [runner](../scripts/test_dma_rows.py)
records source hashes and rejects a result if those files change during the
run. [Current results](../results/ddr_gemm/read4/regression/rows/README.md)
identify the queued planner revision; the
[pipeline results](../results/dma_rows/pipeline/README.md) and
[original evidence](../results/dma_rows/README.md) retain earlier source hashes.

The independent Python oracle enumerates every row word and its useful bytes,
groups addresses by 4 KiB page, then partitions each group into at most 16
words. It checks all burst metadata and masks without reproducing RTL state
transitions. Deterministic seeds `0xD001` through `0xD004` drive the test cases
and handshake delays.

| Recorded coverage | Count |
| --- | ---: |
| Valid / invalid descriptor cases | 550 / 242 across both depths |
| Accepted bursts | 9,257; every length 1..16 |
| Read occupancy | 0..4 in the queued build |

Directed checks include exact DDR-end acceptance, 4 KiB splits, both final-byte
masks, input snapshotting, and errors 7/8 at first, middle and final bursts for
reads and writes. A combined maximum-field invalid request exercises the
widened footprint inputs. Reset is exercised locally in all seven states,
including each validation stage, followed by a fresh successful operation.

Only the default 128 MiB parameter was simulated; the supported 2^32-byte
parameter limit is not a separately tested result. This evidence covers the
sequencer's local contract. Earlier integration results retain their original
source fingerprints; they do not qualify the changed validation pipeline.
Integration and physical timing qualification are separate gates.

The [bounded SAT record](../results/dma_rows/formal/README.md) adds 18 assertions
at read depths one and four through 20 timeframes, including initial reset,
for a fixed four-row descriptor. It checks stalled payloads, credit bounds,
burst metadata and absence of DONE while accepted commands remain outstanding.
Seven decoded witnesses exercise successful transfers, backpressure and fault
draining. This is not an inductive or arbitrary-descriptor proof; its complete
assumptions, exclusions and [reproduction commands](testing.md#bounded-formal-checks)
are recorded separately from the broader simulation regression.
