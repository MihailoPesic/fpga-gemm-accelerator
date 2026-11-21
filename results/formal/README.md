# Scoped Formal Verification Summary (`Yosys 0.69`)

Formal checks run directly on the unchanged production RTL using pinned `yowasp-yosys 0.69` SAT queries (`scripts/formal_buffers.py` and `scripts/formal_dma_rows.py`, harnesses in [`formal/`](../../formal/README.md)). These checks verify targeted control and buffering contracts; they are **not** a full-chip proof of the `P8/T32` accelerator or physical DDR2 PHY.

## Read FIFO and scheduler proofs

21 SAT safety and reachability queries passed (`15` independently decoded VCD/JSON witnesses, verified on both Windows and Linux):

| Module | Configuration | Assertions | Method | Reachable witnesses |
| --- | --- | ---: | --- | --- |
| `gemm_tile_dma_read_queue` | `T=8` and `T=32`, 4 credits | `37` | `8`-frame reset base + separate reset-low `k`-induction (proven at step `2`) | Full 4-credit occupancy, pointer wrap, bank/terminal stalls, stop-then-drain |
| `gemm_tile_scheduler` | `P=4`, `T=8`, `MODE=0` (`1x9x1`) | `36` | `32`-timeframe bounded model check | Complete two-tile job, held load/store offers, fault-then-drain |
| `gemm_tile_scheduler` | `P=4`, `T=8`, `MODE=1` (`1x9x1`) | `33` | `32`-timeframe bounded model check | Above 3 covers + concurrent load and compute in disjoint input buffers |

**Assumptions and limits:**
- Read-queue proof fixes `saved_bt=1`, `saved_buf=1`, and one-beat non-final descriptors while keeping addresses, bank words, 64-bit data, backpressure, and stop requests symbolic.
- Scheduler proof checks a fixed prevalidated two-tile descriptor (`M=1, N=9, K=1`, bases `0x1000/0x2000/0x3000`, stride `64`) against abstract single-operation load, store, and compute engines with unconstrained symbolic delays and faults (no fairness assumption).

## DMA row planner checks

`gemm_dma_rows` passed `2` bounded safety queries and `7` reachability witnesses across `READ_SLOTS=1` and `READ_SLOTS=4`:

| Module | Configuration | Assertions | Method | Reachable witnesses |
| --- | --- | ---: | --- | --- |
| `gemm_dma_rows` | `READ_SLOTS=1` and `4`; fixed `0x0ff0`, stride `64`, `4x20 B` | `18` | `20`-timeframe bounded model check | Read/write completion with stalls, 4-credit peak, simultaneous issue/retire, cancel-then-drain |

The fixed descriptor exercises a 4 KiB page-boundary split and a `0x0f` tail write mask. Arbitrary descriptors, four-state `X` propagation, matrix arithmetic, and AXI/DDR PHY timing are outside these formal harnesses.

## Reproducing locally

```sh
python scripts/formal_dma_rows.py --build-dir build/formal_dma_rows
python scripts/formal_buffers.py --build-dir build/formal_buffers
python -m unittest tb.test_formal_buffers
```
