# Nexys GEMM specification

Target contract, version 1.0, 2026-09-12. These are release requirements, not
claims of completed hardware. See [status](status.md) for implemented work and
[design decisions](decisions.md) for implementation clarifications.
The first release is a P4 BRAM preview; the full target includes DDR2 and P8.

## Board envelope and release goals

Target **Nexys A7-50T**. JTAG detected an `xc7a50t`, and the operator confirmed the Nexys A7 / 50T / CSG324 marking. The full device speed/temperature suffix and physical board revision remain unrecorded. The 50T has 32,600 LUTs, 65,200 flip-flops, 120 DSP slices and 2,700 Kb of block RAM, equivalent to 75 blocks of 36 Kb. The board includes 128 MiB DDR2 and USB-UART. The input-clock constraint is 10 ns, or 100 MHz.[^4][^5]

| Item | Specification / target | Status |
| --- | --- | --- |
| Compute | Signed INT8 multiply, signed INT32 accumulate; P = 4 or 8 at build time. | 4×4 BRAM preview; 8×8 full target. |
| Dimensions | 1 ≤ M,N ≤ 1024; 1 ≤ K ≤ 256 in full release. | Exact tails; no restriction to square matrices. |
| Local tiling | T = 32 macrotiles; T = 8 comparison build; T divisible by P. | Full K is stored for each macrotile. |
| Clock | 100 MHz core target; separate generated MIG user clock. | Actual routed timing must pass. |
| Peak arithmetic | 8×8 at 100 MHz: 6.4 GMAC/s, or 12.8 GOPS when one MAC counts as two operations. | Theoretical ceiling, not measured throughput. |
| Dense kernel target | Aim for ≥7.0 useful GOPS at M=N=K=256, including DDR2 tile transfers and result completion. | Stretch target. Report actual performance and bottleneck if lower. |
| Engineering gates | Bit-exact tests; safe interfaces; complete timing constraints; reproducible build and measurements. | Mandatory for a release claim. |

**Planning budget, not a synthesis result:** allow up to 80 DSPs, 55 RAMB36 equivalents, 24,000 LUTs and 48,000 flip-flops for the release build. An 8×8 array aims for 64 DSPs. The proposed wide operand banks provision about 16 RAMB36 equivalents; C banks about four. MIG, conversion, FIFOs, registers and optional debug consume additional resources. Synthesis may map memories differently; inspect the reports.

**First platform gate:** identify exact board/device; program a minimal design; verify UART; generate the matching board memory configuration; run the MIG example memory test. Record Vivado version, board-file commit, device, memory part, input clocks, MIG clock/width, IP settings and XDC in a platform manifest. Do not guess DDR pin assignments or copy a 100T design into a 50T project unchecked.[^7]


---

## System architecture and ownership

| Block | Responsibility | Boundary |
| --- | --- | --- |
| Host library | Packing, golden model, upload/download, job submission, validation and benchmark capture. | Python; no operating system or soft CPU on the FPGA. |
| Control plane | UART command handling, register bank, descriptor validation, errors and counters. | Single host command outstanding; one accelerator job active. |
| Scheduler | Macrotile iteration, ownership of buffers, overlap and completion. | Single 100 MHz clock domain. |
| Memory movement | Burst-generating read/write DMA, local-bank adapters, queues and response tracking. | One logical 64-bit AXI4 master; independent read/write engines. |
| Compute | Signed MAC PE, skewing, systolic array, bank prefetch and result drain. | No DDR stalls inside an active microtile. |
| Platform | Wrapper, reset coordination and reproducible IP configuration. | AMD MIG and AXI width/clock conversion remain vendor IP. |

Use MIG with its AXI4 interface. Configure the MIG-side AXI width to match its generated native application width where supported. A vendor SmartConnect path adapts the custom 64-bit, 100 MHz master to the actual MIG width and clock. Check the generated configuration rather than assuming its user clock is 100 MHz. The architectural interface to the portable RTL remains fixed.[^7][^8][^9][^10]

**Why the hierarchy matters:** local storage absorbs DDR variability; a prepared microtile then runs with a predictable schedule. The array reuses values spatially, while the macrotile reuses them across multiple array launches. Load, compute and store can proceed independently when buffer ownership permits. Gemmini is a useful reference for these ideas; this is a smaller original SystemVerilog subsystem, with different interfaces and deliberately bounded functionality.[^6]

**Build versus reuse:** own the PE, array, banking, scheduler, DMA policy, verification and measurements. Reuse the DDR controller/PHY and vendor CDC infrastructure. Existing matrix RTL may be retained only after independent tests establish its numerical and cycle behavior. Keep attribution and third-party licenses in the repository.


---

## Mathematical and memory contract

**F-01:** for each accepted descriptor, calculate `C[i,j] = sum(A[i,k] * B[k,j], k=0..K-1)`. Inputs are signed 8-bit two's-complement values. Outputs are signed 32-bit little-endian integers. Every accepted job overwrites C; there is no accumulation into an existing C matrix, bias, activation, scaling, saturation or floating point in version 1.

**F-02:** the host stores B transposed as `BT[j,k] = B[k,j]`. A has M rows of K bytes; BT has N rows of K bytes; C has M rows of 4N bytes. The transpose/packing cost belongs in the host-inclusive benchmark. Do not silently describe a pretransposed input API as accepting arbitrary raw B layouts.

| Field | Rule |
| --- | --- |
| M, N, K | Unsigned descriptor fields; M,N in 1..1024 and K in 1..256. Reject zero or oversized dimensions before issuing memory requests. |
| A_BASE, BT_BASE, C_BASE | 32-bit byte addresses. All bases must be 64-byte aligned and lie inside the configured DDR window. |
| A_STRIDE, BT_STRIDE | Bytes between rows; multiples of 64; at least K. Read each valid row for round_up(K,8) bytes; ignore padded elements in hardware. |
| C_STRIDE | Multiple of 64; at least 4N. Write precisely the 4N useful bytes in each row with AXI byte strobes. |
| Allocated regions | Conservatively treat allocations as base + rows × stride. Validate using widened arithmetic; full allocations must fit in [0,128 MiB) and be pairwise disjoint. |
| Padding / tails | Padded input bytes may contain arbitrary data. Mask them. Padded rows/columns inject zeros. Never modify C row padding or external guard regions. |

**F-03:** K is not tiled through external partial sums in this release. Keep the entire reduction dimension in local input buffers. K ≤ 256 is an explicit useful boundary that avoids introducing an accumulator spill protocol before the rest of the system works. M and N can still span many macrotiles.

**F-04:** the magnitude of one product is at most 16,384; an absolute sum bound is 256 × 16,384 = 4,194,304, safely within signed 32 bits. Explicitly sign-extend the product before adding. The Python oracle must cast operands to a sufficiently wide integer type before matrix multiplication; an INT8 NumPy result is not a valid reference.

**F-05:** macrotiles visit row origins 0,T,2T,..., then column origins 0,T,2T,... within each row band. For an edge macrotile, let r=min(T,M-i0) and c=min(T,N-j0). Launch only ceil(r/P) × ceil(c/P) nonempty microtiles. Within each launch, mask the unused PE rows/columns. Both overlap modes must produce identical outputs.

Example address formulas: `A_BASE + i*A_STRIDE + k`; `BT_BASE + j*BT_STRIDE + k`; `C_BASE + i*C_STRIDE + 4*j`. Address calculations and byte ordering are part of the testable public contract.


---

## Compute array and bank layout

**D-01:** use an output-stationary P×P array. A travels right, B travels down, and each PE owns one accumulator for a microtile. Each hop forwards operands and validity through one register. The reference PE has a registered signed product followed by a registered accumulate. Try to infer one DSP48 per PE and check the actual mapping.[^11]

**D-02, reference cycle convention:** launch at edge 0 clears accumulators and invalidates the product pipeline. The first A/B pair is sampled by PE(0,0) at edge 1. Pair k reaches PE(r,c) at edge 1+k+r+c. It commits to that accumulator one edge later. After the final commit anywhere in the array, drain one complete PE row per cycle into C banks. The final drain edge is K+3P-1. Any added retiming must update this contract, its cycle model and tests together.

The feeder skews A row r by r cycles and B column c by c cycles. A simulation-only k tag checks that paired operands belong to the same reduction step. Reserve the complete destination microtile before launch. Input prefetch completes before launch; an active microtile has no external ready/valid stalls. A debug clock-enable is optional and must freeze every operand, valid, product, accumulator and schedule register coherently.

| Memory | Mapping for local row/column q and reduction index k |
| --- | --- |
| A / BT bank | bank = q mod P. A uses local row q; BT uses local output column q. |
| A / BT 64-bit word | word = buf × (T/P) × 32 + floor(q/P) × 32 + floor(k/8). Byte lane = k mod 8. buf is 0 or 1; KMAX is 256. |
| Read/write ports | DMA writes complete 64-bit words. Compute uses a separate read port and extracts one signed byte per bank per cycle from prefetched words. |
| Word prefetch | Keep current and next 64-bit words per active bank. Fetch ahead of every eight-element boundary; account explicitly for synchronous BRAM read latency. |
| C bank | bank = local_column mod P. word32 = buf × (T²/P) + local_row × (T/P) + floor(local_column/P). |
| C access | Drain P results into distinct banks each cycle. The write DMA combines two adjacent 32-bit results into a 64-bit beat; mask the upper word for an odd tail. |

**Why 64-bit bank ports:** byte-wide banks would serialize a DDR beat into many writes to one bank. Wide words let the DMA deposit eight input bytes per accepted cycle, while compute consumes them across eight cycles. With P=8,T=32, the 16 operand banks are each 256×64 bits including both buffer sets: 32 KiB logical operand storage. The C buffers add 8 KiB. Physical BRAM usage is larger because of banking and supported primitive shapes.

An independent address enumeration checked that the proposed operand map is collision-free for P∈{4,8}, T∈{8,32}, both buffers and all k=0..255, and that each microtile reads distinct banks. This is an analytical check of the plan, not RTL or board validation.


---

## DMA and AXI transaction contract

**M-01:** expose AXI4 memory-mapped, 32-bit byte addresses, 64-bit data and 8-bit WSTRB at 100 MHz. Use INCR bursts only, beat size 8 bytes, burst length 1..16 beats and one constant ID. Address, data and response channels use their independent AXI handshakes. Vendor infrastructure supplies width and clock conversion.[^7][^8][^9][^10]

| Requirement | Implementation and acceptance condition |
| --- | --- |
| Burst splitting | Split at row end, 16 beats and the next 4 KiB boundary. Every address is 8-byte aligned. No transaction crosses a 4 KiB boundary. |
| Read engine | Start with one outstanding burst; full release supports up to four in-order read bursts on one ID. Maintain a metadata FIFO mapping each response to bank, buffer and word address. |
| Read credit | Reserve storage/queue capacity before issuing AR. R beats must never overwrite an unconsumed beat. Route in order; assert expected RLAST and beat counts. |
| Write engine | One outstanding write burst is sufficient for version 1. Assemble a complete burst in a FIFO before issuing it. Retain its C-buffer ownership until its B response is accepted. |
| Channel independence | Do not assume AWREADY and WREADY coincide. Assert AWVALID/WVALID from available work, hold each payload stable until its own handshake, and track each channel separately. |
| Responses | Check every RRESP/BRESP. Complete a job only after all result writes have received successful B responses and all queues/outstanding counts are empty. |
| Input padding | Load round_up(K,8) bytes per valid A/BT row. Hardware masks k≥K; do not read an invalid matrix row to fill a padded PE lane. |
| Output padding | Tail WSTRB enables exactly the useful result bytes. Never use a full unmasked beat for a lone final INT32 element. |
| Host access | Host memory commands reuse the memory path only while the engine is idle. They finish before START can be accepted. No concurrent host mutation of active matrices. |

**M-02:** choose explicit ordinary-memory sideband constants in the wrapper: LOCK=0, CACHE=0, PROT=0, QOS=0, REGION=0; omit unused USER channels or tie them to zero. Configure the path consistently. Do not infer correctness from tied-off sideband signals while ignoring response or burst semantics.

**M-03:** keep a behavioral AXI RAM at this boundary for most simulation. Add randomized delays on all five channels and error injection. Then run a smaller integration test with generated vendor simulation models. Open-source simulation of the portable core is not a simulation of the physical DDR interface.[^16]

Measure read-only, write-only and mixed traffic with the actual row and burst patterns. A 64-bit bus at 100 MHz carries at most 800 MB/s in each continuously transferring channel. The shared DDR device, bridge behavior and command overhead impose a separate combined limit. Do not assume simultaneous 800 MB/s reads and writes from the memory.


---

## Scheduling, completion and failure behavior

| Object | Ownership transitions | Invariant |
| --- | --- | --- |
| Input buffer set | FREE → FILLING → READY → COMPUTING → FREE | Load and compute never own the same buffer set simultaneously. |
| Output buffer set | FREE → COMPUTING → READY_TO_WRITE → WRITING → FREE | Reserve a whole macrotile before compute; free only after its last successful write response. |
| Macrotile | Allocate → load → compute all nonempty microtiles → store → retire | Each macrotile retires once; no duplicate or missing output addresses. |
| Job | Validate → active → all writes acknowledged → DONE | An invalid descriptor issues no DMA; an error never produces a success completion. |

**S-01:** in overlap mode, preload macrotile t+1 while computing t and writing a completed predecessor whenever independent buffers are available. Use exactly two operand sets and two result sets. A stalled write path may delay the next launch; it cannot force overwriting a live result or stopping an already launched microtile. In serial mode, load, compute and store each macrotile in order with no overlap.

**S-02:** the scheduler must reserve output space before consuming a READY input set. Read admission depends on available input capacity; write progress depends only on completed C data and the memory interface. Never make draining a completed output depend on loading the next input. This avoids a circular buffer dependency.

**S-03:** keep the compute/control logic on one clock. Cross AXI through vendor infrastructure. Synchronize the single-bit DDR calibration status appropriately. If any extra multi-bit status must cross domains, use a handshake snapshot or a supported CDC primitive, not one synchronizer per data bit. Preserve and review the bridge's CDC constraints.[^12][^13]

**S-04:** coordinate resets across DMA, conversion and MIG. Deassert each domain's reset synchronously after its clocks are stable. Block START until the platform is ready. A cold/platform reset invalidates all jobs, counters, ownership and host assumptions about matrix contents. Do not clear all BRAM data on reset; clear its valid/ownership state. Do not reset only the master during an outstanding AXI transaction.

**S-05:** descriptor or command errors are recoverable and cause no memory effects. A non-OKAY memory response, protocol inconsistency, lost calibration or watchdog event latches a fatal job error. Stop issuing new transactions, retain obligations for already issued transfers, and drain them if the interface permits. Never withdraw a stalled VALID to “abort.” Fatal state requires coordinated platform reset before another job; partially written C is invalid.

**S-06:** use a configurable no-progress watchdog, default 10,000,000 core cycles (100 ms at the target clock). Reset its timer on a relevant transaction handshake or compute progress while work is pending. A timeout reports the fault; it is not permission to violate AXI. Test with a shorter configured threshold in simulation.

DONE is sticky after success. A valid START or idle CLEAR_STATUS clears DONE and recoverable ERROR state; fatal errors require platform reset. Completion records the job ID and freezes counters before software observes DONE. JOB_CYCLES is the difference between core timestamps at valid START acceptance and the final successful B handshake. There is no mid-job software abort in version 1.


---

## Register interface and measurement semantics

**C-01:** the UART command processor accesses a simple local register request/response interface: 16-bit byte address, read/write, 32-bit write data, 4-bit write strobe, valid/ready; response has 32-bit read data, 16-bit status and valid/ready. One request may be outstanding. Require aligned full-word writes; reject partial or unaligned accesses. This local interface is not advertised as AXI-Lite.

| Address | Register | Definition |
| --- | --- | --- |
| 0x00 / 0x04 | ID / VERSION | ID=0x314D474E (bytes N,G,M,1); version=0x00010000. |
| 0x08 | GEOMETRY | P in bits 7:0; T in 15:8; KMAX=256 in 31:16. |
| 0x0C | STATUS | Bits: 0 READY, 1 BUSY, 2 DONE, 3 ERROR, 4 DDR_READY, 5 RESET_REQUIRED; others zero. |
| 0x10 | CONTROL | Write exactly 1 for START or 2 for CLEAR_STATUS. Other values invalid. CLEAR_STATUS is idle-only and cannot clear a fatal reset requirement. |
| 0x14 | JOB_ID | Unsigned 32-bit software identifier. |
| 0x18 / 0x1C / 0x20 | M / N / K | Unsigned dimensions; full validation on START. |
| 0x24 / 0x28 / 0x2C | A_BASE / BT_BASE / C_BASE | 32-bit DDR byte addresses. |
| 0x30 / 0x34 / 0x38 | A_STRIDE / BT_STRIDE / C_STRIDE | 32-bit byte strides. |
| 0x3C | MODE | Bit 0: overlap enabled; other bits zero. Reset default 0. |
| 0x40 / 0x44 | ERROR_CODE / LAST_JOB_ID | Latest reported engine error; ID of most recently accepted job. |
| 0x48 / 0x4C | CORE_HZ / WATCHDOG_LIMIT | Build clock in Hz; writable no-progress limit in cycles, minimum 1. |
| 0x80 / 0x88 | JOB_CYCLES / COMPUTE_CYCLES | 64-bit frozen counters. Job: accepted valid START to final successful B handshake. Compute: cycles spent in active microtile schedules. |
| 0x90 / 0x98 | READ_BEATS / WRITE_BEATS | 64-bit counts of accepted R and W beats, excluding host transfers. |
| 0xA0 / 0xA8 | WRITE_VALID_BYTES / INPUT_WAIT_CYCLES | 64-bit sum of WSTRB popcounts; cycles with a pending next compute launch held for input availability. |
| 0xB0 / 0xB8 | READ_STALL_CYCLES / WRITE_STALL_CYCLES | 64-bit counts of RVALID without RREADY and WVALID without WREADY. They do not alone measure DDR command latency. |

**C-02:** configuration registers are writable only when idle; valid START atomically snapshots them. READY means idle, calibrated, no host memory command active and no fatal error. A repeated START while busy returns BUSY and does not enqueue a second job. Counters occupy low word at the stated address and high word at +4; software reads frozen completed-job snapshots, not live torn values.

**C-03:** unknown register addresses return BAD_ADDR. Read-only writes return BAD_CMD. Status codes: 0 OK; 1 BAD_CMD; 2 BAD_ADDR; 3 BAD_DESC; 4 BUSY; 5 NOT_READY; 6 SEQ_CONFLICT; 7 MEM_RESP; 8 PROTOCOL; 9 WATCHDOG; 10 CALIB_LOST. Codes 7..10 are fatal. Invalid descriptor rejection leaves the engine idle, reports BAD_DESC and performs no DMA.


---

## Host transport and software contract

**H-01:** use USB-UART, 8N1, initially 115200 baud; validate a 1,000,000-baud build for the final demo. Baud is a build setting, not a negotiated runtime mode. UART is a control and data-loading link; core performance comes from on-board counters. At 1 Mbaud, 8N1 permits at most 100,000 payload bytes/s before framing overhead.

**H-02:** frame each binary packet with COBS and terminate it with 0x00. Decoded layout: version:u8=1, opcode:u8, sequence:u16, payload_length:u16, payload:0..256 bytes, CRC32:u32. Multi-byte fields are little-endian. CRC is the reflected IEEE CRC-32 over the decoded header and payload: polynomial 0xEDB88320, initial value 0xFFFFFFFF, final XOR 0xFFFFFFFF; test vector “123456789” gives 0xCBF43926. Maximum decoded length is 266 bytes; provision a bounded encoded receive buffer of 270 bytes.[^18]

| Opcode | Request payload | Success response data |
| --- | --- | --- |
| 0x01 PING | A 32-bit nonce. | Echo nonce, ID and VERSION (three u32 values). |
| 0x02 READ_REG | Register address:u16. | Value:u32. |
| 0x03 WRITE_REG | Address:u16, value:u32. | Empty; START acknowledges acceptance after descriptor validation, not job completion. |
| 0x04 MEM_READ | DDR address:u32, length:u16. | Exactly length bytes; length 8..240, multiple of 8; address 8-byte aligned. |
| 0x05 MEM_WRITE | DDR address:u32, length:u16, data. | Empty; same length/alignment rules; respond after successful B completion. |

Responses use request opcode OR 0x80, echo the sequence, and prepend status:u16 to the data. All responses fit the 256-byte payload bound. Reject invalid lengths, CRC, encoding or version without executing a command; discard an oversized frame through the next delimiter. A correctly framed unknown opcode receives BAD_CMD. UART corruption must never trigger a partial register or memory write.

**H-03:** use stop-and-wait transport. Cache the last complete valid request bytes and response. An exact duplicate request replays that response without re-executing START or a write. A reused sequence with different request bytes returns SEQ_CONFLICT. New sequences replace the cache after completion. Reset clears the cache; after reconnect/reset the host rechecks platform state and reloads inputs rather than blindly replaying a timed-out START.

**H-04:** Python API: pack_inputs(A,B), write_memory(), read_memory(), configure(desc), start(job_id), wait(job_id), read_output(), validate(), benchmark(). Use NumPy INT64 or Python integers for the oracle, then check INT32 range. Keep deterministic seeds and explicit binary serialization tests independent of the RTL driver. The CLI must return nonzero on mismatch, timeout or hardware error.

Provide a deterministic one-command demo that checks the bitstream identity, loads matrices, runs the job, compares every result and saves JSON plus CSV. Larger matrices can remain in DDR between repeated jobs; disclose this residency. Do not replace full correctness checking with a checksum alone. Record packing, transfer, compute and validation times separately.


---

## Verification plan and release acceptance

**V-01:** write tests from the mathematical and interface contracts, before optimizing RTL. Use cocotb with a compatible simulator, an independent wide-integer oracle and cocotbext-axi RAM models. Keep versions pinned after a successful local smoke test. Vendor-IP integration uses Vivado simulation separately.[^15][^16]

| Layer | Required evidence |
| --- | --- |
| PE arithmetic | All 65,536 signed INT8 input pairs; accumulation sequences at extrema, zeros and alternating signs; reset and product-valid bubbles. |
| Array / feeders | P=4 and P=8; K=1,2,7,8,9,31,32,33,255,256; directed skew tests, isolated nonzero elements and 500 seeded random microtiles. Check the final drain edge and operand tags. |
| Banking / tiling | M,N around P and T boundaries; independent address scoreboard; arbitrary nonzero input padding; sentinel bytes around C and in its row padding. |
| AXI / DMA | Independent channel stalls; four reads outstanding; delayed B; all burst splits; addresses near 4 KiB boundaries; injected RRESP/BRESP errors; RLAST and count checks. |
| System | At least 100 seeded mixed jobs with dimensions mostly 1..97, K up to 256, both overlap modes, repeated starts, back-to-back jobs and invalid descriptors. Include maximum-boundary cases separately. |
| Transport / control | Truncation, bad CRC, dropped/inserted bytes, duplicate START, same-sequence conflict, busy writes, reset between commands and stuck-memory watchdog. |
| Formal subset | Prove FIFO occupancy/order and output stability under backpressure; check buffer ownership/exclusion in a reduced scheduler model. Include covers and state all assumptions. |

**V-02:** define functional coverage bins for P, K boundaries, each M/N tail class, mode, outstanding-read occupancy, independent AW/W delays, each error code and each ownership transition. Seed counts are a minimum exercise set, not a claim of exhaustive verification. Preserve failing seeds and add permanent regressions. Deliberately insert at least three defects, such as a signedness error, early buffer reuse and missing tail strobe, and show that the suite catches them.

**V-03:** assertions include stable payload while VALID is stalled, no illegal ownership combination, no read of an unready tile, no write outside the authorized C region, no success before final write response, and no successful job duplication. Formal results must name the proven module, configuration, assumptions and whether the run is bounded or inductive; do not call the full accelerator “formally verified.”[^17]

**V-04, physical release:** routed timing passes setup and hold at the declared clock. Review clock interactions, CDC, reset paths, I/O constraints and all unconstrained paths; no unexplained critical warnings. Save utilization and timing summaries, plus the worst path explanation. Missing constraints can make an apparently positive slack report misleading.[^12][^14]

Run full output comparisons on the board for small/odd shapes and representative large cases, including M=N=1024,K=256 at least once. Repeat a selected mixed workload for 30 minutes without mismatches. Treat this as a repeatability test, not a lifetime reliability certification.


---

## Benchmarks that demonstrate architectural reasoning

**B-01:** publish three separate measurements: **resident-array** compute with already prepared local inputs; **DDR-resident job** including every tile load, result write and final B response; **host-inclusive** packing, upload, job and download. Report validation time separately. Never label the arithmetic ceiling or a counter that excludes transfers as end-to-end speed.

| Case / quantity | Analytical result | Interpretation |
| --- | --- | --- |
| 8×8, 100 MHz peak | 12.8 GOPS = 2 × 64 × 100 MHz | Requires useful work in every PE on every cycle. |
| One 32×32 output tile, K=256 | 524,288 useful operations | Independent of implementation. |
| Reload inputs for every 8×8 microtile | 69,632 bytes: 65,536 inputs + 4,096 outputs | Algorithmic transfer volume for the comparison build. |
| Reuse inputs across the 32×32 tile | 20,480 bytes: 16,384 inputs + 4,096 outputs | 4× less input traffic; 3.4× less total traffic in this example. |
| Arithmetic intensity after reuse | 25.6 operations per transferred byte | Excludes command overhead, extra DDR granularity and host traffic. |
| If mixed bandwidth is 350 MB/s | Bandwidth roof = 8.96 GOPS | Scenario, not a measured board bandwidth. |
| K=256 array schedule model | 282 cycles between launches if prefetch/control add three cycles to the final-drain edge; about 11.62 GOPS | Illustrative schedule assumption; replace with the implemented cycle model. |

**B-02:** make controlled comparisons at P=8 and the same routed clock: A = T8, overlap off; B = T32, overlap off; C = T32, overlap on. A→B isolates reuse; B→C isolates overlap. Use P4 as the initial/fallback implementation and an optional later scaling comparison. Do not attribute a speedup to tiling while also changing clock, precision and matrix shape.

**B-03:** test M=N=32,64,128,256 with K=16,64,256; include (M,N,K)=(31,33,17),(65,63,255),(1,64,256) and (64,1,256). Report useful throughput as 2MNK × core_hz / job_cycles. Useful utilization is MNK/(P² × job_cycles); it includes tail and scheduling losses. Report latency for small shapes, where high utilization is not expected.

For each build/case, run at least 30 completed jobs after calibration, and report median plus minimum/maximum and failures. Save raw counters, workload seed, actual clock, exact bitstream/commit, tool versions, resource use and timing. Plot useful GOPS by K/shape and show the transfer-volume change. Any optional CPU comparison must include host movement and identify software, hardware and data types.

Use independent transfer and compute counters to explain measured throughput, including bandwidth, burst latency, array fill/drain, tail waste and host I/O. Report any gap from the 7 GOPS target with the limiting stage and supporting measurements.

## References

[^4]: [Digilent: Nexys A7 overview](https://www.farnell.com/datasheets/4557879.pdf). Manufacturer document mirrored by Farnell, printed 8 July 2025. Hardware resources and onboard interfaces.
[^5]: [Digilent: Nexys-A7-50T-Master.xdc](https://github.com/Digilent/digilent-xdc/blob/master/Nexys-A7-50T-Master.xdc). Official constraint file; input clock specified with a 10 ns period.
[^7]: [AMD: UG586: Zynq 7000 SoC and 7 Series Devices Memory Interface Solutions](https://docs.amd.com/r/en-US/ug586_7Series_MIS). Version 4.2, 13 November 2024. MIG generation, example designs, memory calibration and integration.
[^8]: [AMD: UG586: AXI4 Slave Interface Parameters](https://docs.amd.com/r/en-US/ug586_7Series_MIS/AXI4-Slave-Interface-Parameters). 32-bit addressing; selectable AXI data widths; recommends matching APP_DATA_WIDTH for performance.
[^9]: [AMD: PG059: Clock Conversion](https://docs.amd.com/r/en-US/pg059-axi-interconnect/Clock-Conversion). AXI infrastructure reference, version 2.1. Use the documentation for the selected installed IP release.
[^10]: [AMD: PG247: Width Conversion](https://docs.amd.com/r/en-US/pg247-smartconnect/Width-Conversion). SmartConnect automatically adapts differing endpoint widths. Width conversion is supplied by vendor IP.
[^6]: [UC Berkeley Architecture Research: Gemmini: Berkeley's Spatial Array Generator](https://github.com/ucb-bar/gemmini). Architecture reference for systolic computation, local storage, DMA and overlapped execution. This project does not port Gemmini.
[^11]: [AMD: UG949: Coding for Optimal DSP and Arithmetic Inference](https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Coding-for-Optimal-DSP-and-Arithmetic-Inference). Check inferred hardware and register placement in synthesis; one DSP per PE is a design goal.
[^16]: [Alex Forencich and contributors: cocotbext-axi](https://github.com/alexforencich/cocotbext-axi). AXI master, slave and RAM simulation models; extend the test environment for error injection.
[^12]: [AMD: UG949: Clock Domain Crossing](https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Clock-Domain-Crossing). Reference for CDC structures and analysis. Vendor bridge constraints must remain intact.
[^13]: [AMD: UG949: Multi-Bit CDC](https://docs.amd.com/r/en-US/ug949-vivado-design-methodology/Multi-Bit-CDC). Reference for coherent multi-bit transfers; independent bit synchronizers do not make a bus coherent.
[^18]: [Stuart Cheshire and Mary Baker: Consistent Overhead Byte Stuffing](https://www.stuartcheshire.org/papers/COBSforToN.pdf). Author-hosted paper supporting the serial framing choice. The command protocol here is project-specific.
[^15]: [cocotb contributors: Writing Testbenches](https://docs.cocotb.org/en/stable/writing_testbenches.html). Python-driven HDL verification. Pin and record a compatible simulator/cocotb combination during implementation.
[^17]: [YosysHQ: SBY: Getting started](https://yosyshq.readthedocs.io/projects/sby/en/latest/quickstart.html). FIFO formal tutorial, bounded checking, proof and cover examples. Proof claims must specify assumptions and scope.
[^14]: [AMD: UG906: Unconstrained Paths Section](https://docs.amd.com/r/en-US/ug906-vivado-design-analysis/Unconstrained-Paths-Section). Missing constraints can leave paths unanalyzed. A clean slack number alone is insufficient.
