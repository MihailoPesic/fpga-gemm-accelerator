# Independent tile DMA contexts

Portable checkpoint, 2026-10-04. The new `gemm_tile_dma_duplex` wrapper
reuses the tested adapter as a fixed operand reader and C writer. Descriptor
capture, row planning, busy ownership and held operation responses are
independent; both use the existing burst engine. Production still uses the
serial adapter, with `CONCURRENT_PORTS=0` and MODE=1 rejected.

The [record](record.json) binds the checkpoint's source hashes to exact copied
XML/coverage/summary files and actual process exit records. It identifies
the unchanged qualified FPGA image separately. No new vendor, routed timing
or physical-board result is claimed.

| Suite | Configuration | Result |
| --- | --- | --- |
| [Duplex integration](duplex/summary.json) | P4/P8, T8/T32, READ1/4 | 40 tests; 48 fully checked stored matrices; 12,728 useful outputs |
| [Serial DMA compatibility](serial_tile_dma/summary.json) | P8/T32, READ4 | 7 tests; 27 jobs; 4,414 compared outputs |
| [Serial packet/core compatibility](packet_core/summary.json) | P8/T32, READ4 | 10 tests; 8 jobs; 2,319 compared outputs |
| [Compile checks](lint_console.txt) | Both read depths and all four geometries | Existing production tops and duplex wrapper compile successfully |

Five exact named cases run in every duplex configuration:

- Three tile owners: fill input1, compute input0 into result1, and store
  result0. A directed edge accepts both a load word and C read while compute
  is active; independent randomized stalls then resume. Holding B blocks
  store completion while A and a subsequent BT load complete. All three
  output matrices are later checked in full.
- Independent held completions and invalid descriptors: successful DONE in
  one context permits new operations in the other; BAD_DESC has no transfers
  and does not poison its valid sibling. A later fatal does not rewrite an
  already held successful DONE payload.
- Shared admission and first error: external codes normalize as specified;
  the captured code remains frozen after the source changes/deasserts.
  Local reader and writer faults block an unaccepted sibling command when
  its admission gate is released before shared-stop capture.
- A read error while the writer holds AW, W, a C request or C response.
  Offers survive the fault; accepted writes retain their B obligation and
  both local contexts drain.
- A C-response error while the reader holds a bank word and uses every read
  credit. The held word remains stable, accepted commands drain, and a later
  external error cannot replace the first captured code.

The arithmetic oracle generates raw B, explicitly transposes it for DDR and
uses Python integers for each dot product. DDR row addresses are independently
enumerated before page/max-length partitioning. Tests compare every delivered
input word, useful C value, output padding/guard byte, byte strobe, WLAST and
write-response count. Nonzero input padding and signed -128 operands are
included. Both READ1 and READ4 reach their configured read-credit occupancy.

The runner requires exactly the five test names and rejects missing,
unexpected, duplicate, failed or skipped tests. It checks normalized source
hashes before each configuration and after execution and records the actual
Python, simulator and dependency versions. Complete simulator logs and
generated binaries stay under ignored `build/`.

This is a behavioral AXI integration test, not a complete overlap scheduler
or physical DDR test. Buffer-to-tile tags, independent scheduler cursors,
job-level counters/final-B completion and host exclusion remain integration
work. The current routed image still reports the measured serial result;
these tests do not establish new resource use or throughput. See the
[module contract](../../docs/tile-dma.md#independent-load-and-store-contexts)
and [next release gates](../../docs/status.md#next-gates).
