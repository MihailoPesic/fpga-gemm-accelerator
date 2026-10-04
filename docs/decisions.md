# Design decisions

The [technical specification](specification.md) defines the project target.
These decisions clarify its implementation contracts. Saved compute evidence
is in [results/core](../results/core/README.md); current integration status is in
[status.md](status.md). Changes to these decisions require matching tests.

1. **Platform baseline.** Target xc7a50ticsg324-1L. Preserve the working
   200 MHz DDR2 / 50 MHz native UI configuration for the initial transition.
   These are the saved project's settings, not a claim that 200 MHz is the
   board's maximum memory speed. The AXI MIG/converter configuration is
   generated and verified separately. Native `app_addr` values must not enter the new
   byte-addressed descriptor or host API.
2. **PE and cycles.** Use a signed 32-bit registered product and check one
   DSP48E1 per PE in synthesis. `docs/compute.md` makes the core's input/drain
   capture edges explicit. A registered drain controller preserves the stated
   final edge K+3P-1; prefetch overhead belongs to the caller's cycle model.
3. **Transport widths.** Use the specification's six-byte
   header (u8 version, u8 opcode, u16 sequence, u16 payload length), u32 CRC,
   u16 register address/status, and u32 memory address/u16 length. Retain those
   little-endian widths. Validate host MEM bounds using widened arithmetic,
   including end <= 128 MiB, before issuing a request.
4. **Retries.** Cache both the active request and the last completed exchange.
   An active exact duplicate coalesces; a same-sequence different request
   returns SEQ_CONFLICT without replacing the active/completed request.
   Completion replaces the previous cache. A cached BUSY/NOT_READY response is
   replayed for an exact retry; software uses a fresh sequence to try again.
   Only the active/last exchange is protected, not arbitrarily old traffic.
   Reset clears the retry epoch; software rechecks identity and reloads data.
5. **Identity.** Add read-only BUILD_ID at 0x50 (u32), allocated from a build
   manifest identifying source/configuration. The offline manifest stores the
   finished bitstream SHA-256. Do not attempt to embed the bitstream's own
   self-referential hash. The BRAM preview uses a distinct ID/version and no
   DDR_READY claim; it is not the full DDR register/transport release.
6. **Fatal behavior.** Preserve every already asserted AXI VALID and its
   payload, even before handshake. Complete accepted AW/W obligations and
   consume outstanding responses while the interface permits. Stop admitting
   fresh work; first fatal error wins. Loss of clocks/calibration may prevent
   draining and requires a coordinated platform reset, not a local master
   reset. Keep diagnostic register reads available after a host-MEM timeout.
7. **Reset and errors.** The full-release contract requires coordinated reset
   across master, conversion and MIG,
   overlap reset assertion for at least 16 cycles of the slowest AXI clock,
   and deassert synchronously in each stable domain. UART status remains
   available while calibration is pending. Per-command BUSY/BAD responses must
   not corrupt an active job's error state or completion snapshot.
8. **Full-release completion and counters.** JOB_CYCLES measures accepted START through
   the final successful B handshake at the owned AXI boundary. It does not
   timestamp physical SDRAM cell writes or expose opaque vendor queues.
   COMPUTE_CYCLES increments on each busy edge after launch, including final
   drain: K+3P-1 per completed microtile, excluding launch/prefetch. Status
   polling is not watchdog progress. Freeze all completed-job counters before
   reporting DONE. Error jobs cannot report successful completion.
   The implemented preview ends at its last local C write, and the memory
   diagnostic has its own checked-read completion. Their counters are not
   substitutes for the full DDR GEMM measurement; see
   [completion boundaries](architecture.md#completion-boundaries).
9. **Performance comparisons.** T8 to T32 changes input reuse and result burst
   shape. Byte counts isolate transfer-volume savings; explain both effects
   when attributing runtime improvements. Preserve the same P/clock/shape.
   Seven useful GOPS on 256 cubed needs at least 273.4375 MB/s effective mixed
   tile traffic even with ideal overlap; it remains a measured stretch target.
10. **AXI width and memory clocks.** Keep the custom master at 64 bits and
    100 MHz; use vendor SmartConnect to reach MIG's generated 128-bit,
    50 MHz AXI interface. The retained DDR2 clock is 200 MHz. Own transfer
    policy and protocol handling in RTL; reuse vendor width conversion,
    payload CDC, controller and PHY. SmartConnect packs multi-beat traffic
    and propagates MIG narrow-burst support=0 in this integrated path.
    Single 64-bit transfers, including address 8, are covered by the vendor
    test. The standalone MIG example has a separate narrow-enabled setting;
    copying that flag into the integrated path is not the configuration rule.
    Both bus widths provide 800 MB/s of ideal payload capacity, but shared
    DDR bandwidth and overhead must be measured. Configuration and evidence:
    [axi-platform.md](axi-platform.md),
    [generator](../scripts/create_axi_platform.tcl),
    [vendor pass](../results/ddr_platform/vendor_pipeline/summary.json).
11. **Reset ownership and startup qualification.** The board clock wizard
    supplies core/system and reference clocks; MIG's user-clock reset feeds
    reset controllers for the core and UI domains. Calibration crosses into
    the core through one three-flop status synchronizer; AXI payload CDC stays
    inside SmartConnect. START requires synchronized calibration readiness.
    Issued transfers remain obligations through errors; a local master reset
    is not an abort. The current board evidence uses operator-confirmed
    cold power-up in JTAG mode. Abrupt CPU reset or FPGA reprogramming while
    DDR stays powered is a separate warm-controller event: the vendor model
    still reports clock/CKE violations, despite recovered data comparisons.
    Retain that failed qualification and require fresh cold startup for the
    currently qualified procedure. See the
    [wrapper](../platform/nexys_a7/axi_ddr_platform.sv),
    [reset contract](ddr-diagnostic.md#reset-qualification-remains-open),
    [failed reset probe](../results/ddr_platform/reset_probe/summary.json) and
    [physical check](../results/ddr_platform/board/summary.json).
12. **Register the diagnostic comparison.** The first full DDR diagnostic
    missed 100 MHz setup by 0.727 ns. Its worst path crossed address/pattern
    generation and error control to a register enable. Capture received data,
    expected data and address, then compare on the next cycle. Backpressure
    both read data and completion while that check is pending; the last word
    must be checked before another command or DONE. This costs diagnostic
    cycles but removes that combined path. Focused tests corrupt the last
    word of both a burst and a job; the new implementation passes setup/hold
    at +0.642/+0.027 ns, with the worst path now in transport/CRC control.
    These are observed full-build results, not a promise that arbitrary
    pipelining improves timing. See
    [controller](../rtl/control/gemm_ddr_diag.sv),
    [original timing failure](../results/ddr_platform/timing_probe/summary.json),
    [comparison regression](../results/ddr_platform/pipeline_check/summary.json)
    and [routed result](../results/ddr_platform/routed/summary.json).
13. **Serial row planning before full DMA.** Keep the first row sequencer
    command-only, with one burst awaiting completion. Advancing at command
    acceptance would release work before its data arrived or its write was
    acknowledged. Advance only when the adapter confirms complete read
    delivery or the write's B response; retain buffer ownership meanwhile.
    Split at row ends, 16 beats and 4 KiB boundaries. Apply `0x0f` only to the
    final row beat containing a lone INT32; earlier beats remain `0xff`.
    Validate the rounded touched range with widened arithmetic before issuing
    anything, while leaving full allocation bounds/disjointness to descriptor
    validation. This isolates address and completion errors before introducing
    bank routing, overlap or concurrent reads. The
    [row contract](dma-rows.md) and
    [portable evidence](../results/dma_rows/README.md) cover this boundary;
    they do not establish an integrated DMA or physical transfer result.

14. **Reuse the burst buffers for bank transfers.** The tile DMA adapter
    routes received words directly into operand banks and reads one C pair
    at a time into the existing write buffer. A second burst-sized staging
    memory would duplicate storage without removing the current engine's
    serial port limit. Keep the first integration serial; changing that
    limit and managing concurrent buffer ownership are later changes.
    A local C-response fault cannot abandon an accepted write command.
    Supply the remaining beat count with zero strobes, preserve already
    offered bank requests and wait for terminal memory completion. Partially
    written C remains invalid. See the [tile DMA contract](tile-dma.md).

15. **Validate the whole job before DMA.** The serial controller snapshots
    full-width descriptor fields, checks dimensions/alignment/strides and
    calculates conservative allocation ends with widened arithmetic. All
    three allocations must fit the DDR window and be pairwise disjoint before
    the job is accepted. Row-major macrotiles then use the verified local
    engine and DMA adapter with buffer zero, retaining the full K dimension.
    MODE must be zero in this checkpoint; overlap requires a later ownership
    scheduler. Invalid descriptors produce no memory requests. See
    [ddr-job.md](ddr-job.md) and [its regression](../results/ddr_job/README.md).
16. **Preserve the completion and first-fault timestamps.** Validated START
    establishes the job timestamp. Record the actual final successful AXI B
    handshake even if the adapter's terminal notification is delayed, and
    expose DONE only after that notification and quiescence. Freeze public
    counters at the first fatal event; draining obligations cannot change the
    fault snapshot or produce success. After calibration has first succeeded,
    its loss latches a fatal reset requirement even while idle. Startup before
    the first calibration remains NOT_READY. The serial board implementation
    has [separate measured results](../results/ddr_gemm/board/README.md).
17. **Capture BUSY at local command acceptance.** A START received during a
    job must return BUSY even if that job finishes before register decoding.
    Capture the busy state alongside the local request and also check the
    current state before dispatch. Apply the same rule to CLEAR_STATUS and
    configuration writes. This prevents decoding latency from becoming an
    implicit command queue. An initially idle START still waits for the job
    controller's descriptor-validation response before the register command
    acknowledges it. See [ddr-registers.md](ddr-registers.md).
18. **Share the burst engine by whole-operation ownership.** Host memory
    commands may use the existing read/write buffers only while the job engine
    is idle. Reserve host ownership before offering a burst and retain it
    through terminal completion and quiescence. AXI quiescence alone does not
    prove that an accepted write's local collection has finished. During a
    fatal drain, diagnostics may replace packet storage, so captured write
    data have their own retained storage. A watchdog reports the error without
    freeing live ownership or withdrawing AXI VALID. Gate job traffic/progress
    monitors with the owner so host transfers cannot enter job counters.
    See [ddr-core.md](ddr-core.md) and
    [complete packet evidence](../results/ddr_core/README.md).
19. **Version the serial DDR checkpoint separately.** Report ID `0x314d474e`
    and VERSION `0x00000100` for the serial command subsystem. MODE remains zero
    and full v1 overlap/concurrency compliance is not advertised. The host checks
    identity/geometry/build/clock before work, rechecks descriptors before START
    and reads completed counters before another START. Manifest verification
    checks the selected local bitstream SHA-256 against its file; UART reports
    BUILD_ID, not a bitstream hash readback. Preserve full output comparisons,
    disclose resident-input repetitions and separate host validation/guard time
    from job and transfer measurements. See [host-gemm.md](host-gemm.md).

20. **Prepare bounds and completion predicates before active transfers.** The
    first complete serial DDR GEMM route missed 100 MHz setup by 2.761 ns.
    Its longest path combined the row-offset multiply, widened footprint sum
    and validation in one cycle. Register those calculations in separate
    stages before admitting a burst. The same route exposed variable watchdog
    subtraction and final-band arithmetic driving wide counter snapshot
    enables. Prepare thresholds during configuration capture and final-band
    flags when tile origins change. For idle host-command admission, evaluate
    only faults possible without an active memory owner. Retain the active
    fault/drain logic and first-fault edge. These changes require fresh portable
    regressions, vendor integration and full routing; an isolated controller
    pass cannot qualify its integrated fanout and routing.

21. **Check the vendor reference before DDR startup.** A complete UART/MIG
    simulation stopped because its reference dot product returned an unknown
    value. A DUT-free reproducer isolates the nested-function expression in
    XSim 2026.1; the same expression passes in Icarus. Separate signed operand,
    product and accumulation assignments preserve the mathematics and pass
    both simulators. Verify the fixture's 16 directed results before releasing
    reset, and run the extracted-function regression after reference changes.
    Retain the original failure and require a new complete integration pass;
    a corrected reference does not qualify unexamined hardware outputs. See
    [the failure evidence](../results/ddr_gemm/vendor_oracle_failure/README.md)
    and [testing workflow](testing.md).

22. **Attribute transfer cycles before changing the schedule.** Partition
    accepted START through the final successful B edge into disjoint controller
    phases, and reconcile them with the frozen counters. Record individual
    burst handshakes to separate first-response delay, validated-buffer copy,
    C gathering and write-response wait. T8/T32 comparisons keep P, clock,
    layout and inputs fixed. Known per-burst latency injection checks both
    individual intervals and total job sensitivity. Delayed RAM callbacks
    preserve their post-edge queuing phase so simulator scheduling cannot
    silently shorten the requested delay. Complete ordered traces remain
    local; compact tables retain event endpoints, counts and gap histograms.
    See [cycle attribution](ddr-core.md#cycle-attribution).

23. **Offer the next C pair on command or word acceptance.** The original
    portable trace measured five collection cycles per 64-bit result word.
    Its separate preparation state performs no normal-path work. Enter the
    C-request state directly when accepting a write command or a nonfinal
    collected word. Retain preparation for zero-strobe fault filling, and
    retain a registered response and data offer. Faults on those acceptance
    edges must preserve any request/data already offered and cannot release
    C ownership before the terminal write response. Compare the same workload
    before and after the change: gathering must save exactly one cycle per
    write beat, with unchanged arithmetic, addresses, masks and all other
    transfer intervals. Changed next-state logic requires new full routing;
    the earlier bitstream remains qualified only under its original identity.

24. **Measure array scaling with the transfer path fixed.** The P4/P8 board
    comparison keeps all 28 build-source hashes, six host hashes, matrix
    bytes, layouts, clock and serial schedule fixed. Dense active compute
    falls from 17,088 to 4,464 cycles, but the measured DDR-job median falls
    only from 34,103 to 21,281.5 cycles: 1.60x DDR-job speedup. Input-loading
    phases occupy 61.9% of the P8 job; their counter includes planning,
    response latency and validated-buffer delivery, so this is not proof
    of saturated DDR bandwidth. Target read issue/delivery concurrency
    before inter-tile overlap, which cannot improve a one-macrotile case.
    Keep all small-shape distributions visible: a larger array can increase
    scalar latency through its longer scheduled fill/drain. See
    [the physical comparison](../results/ddr_gemm/p8_scaling/README.md).

25. **Reserve read metadata before issuing concurrent commands.** In the
    four-read build, command acceptance reserves one metadata entry and one
    complete burst buffer. The issue cursor advances independently of the
    ordered retirement cursor. An entry is released only after its final bank
    word and terminal completion have been consumed. Stop new reads on any
    fatal job or DMA condition; cancel never-offered addresses while retaining
    held AXI VALID and response obligations. A calibration-loss regression
    holds the first AR and queues three more commands, then checks that only
    the held AR reaches DDR. Keep writes and the one-read schedule unchanged.
    Compare identical P8/T32 workloads, traffic and compute/store phases before
    drawing a performance conclusion; behavioral cycle savings require a
    separate routed build and physical measurement.

26. **Publish host errors from the first-error register.** The first four-read
    route missed setup by 0.470 ns on a queued-read protocol/progress/watchdog
    path into the wide host response payload. Keep immediate fault detection
    for memory admission and draining, but consume its registered error for
    response-buffer capture and publication. Error replies take one extra
    edge. Suppress success on a simultaneous completion/fault edge and retain
    calibration checking until the packet completion decision. Verify these
    boundaries under response backpressure before repeating vendor and routed
    qualification. See [the failed route](../results/ddr_gemm/read4/initial_timing/README.md).

27. **Register host burst geometry before command admission.** The registered
    error-response revision reduced failing setup endpoints from 882 to 14,
    but its worst remaining path combined host length planning with AXI command
    validation. Capture the first length at packet admission and subsequent
    lengths at successful nonfinal completion. Keep addresses, remaining words
    and burst lengths consistent, including during stalls and fatal draining.
    A 16-beat burst can cross a page boundary only when starting in its last
    128 bytes; use that bounded page calculation. Add no planning state or
    watchdog cycle. Independently enumerate transfers at every aligned start
    in that region before repeating vendor and routed qualification.

28. **Reserve local banks for the entire macrotile.** Add opt-in disjoint
    load/read ports while keeping the default serial behavior. The active
    input set remains reserved between microtiles because later launches reuse
    its operands. Input and output buffer IDs are independent namespaces;
    tests must exercise different IDs in both directions. Capture the accepted
    result-read buffer and retain it through BRAM latency and response stalls.
    A new START may use the other result set, but cannot replace the owned set
    on the response-consume edge. Verify arithmetic and the existing schedule
    before integrating DMA concurrency. The production scheduler still rejects
    overlap until independent operation contexts and tagged tile ownership
    are implemented. See [the local-port evidence](../results/tile_overlap_ports/README.md).

29. **Separate DMA operation contexts before overlapping macrotiles.** Reuse
    the tested tile adapter twice, with a fixed operand reader and C writer.
    Keep descriptor capture and held completion independent; a delayed B must
    not prevent another operand operation. Share fatal state through a
    registered first-error latch because each child's fatal includes its
    external stop input. Immediate guards stop both unaccepted local-command
    handshakes while accepted bank, data and terminal offers drain. AXI VALID
    remains owned by the burst engine. Test three simultaneous buffer owners
    and faults with stalled offers before adding scheduler cursors. Keep this
    wrapper outside the production path until tagged ownership, final-B
    completion and physical timing are qualified. See the
    [duplex contract](tile-dma.md#independent-load-and-store-contexts).

30. **Keep tile tags independent of buffer IDs.** Input and result sets become
    free at different edges. Capture each tile's tag, shape and DDR addresses
    with its buffer owner, and choose a free result independently of the input
    ID. Separate admission, compute and retirement cursors preserve row-major
    order while load/compute/store advance independently. MODE=0 waits for
    retirement before admitting another tile; MODE=1 uses available input
    capacity. A stalled writer may block a new launch but cannot pause active
    compute or release live C data. Internal scheduler DONE waits for both
    memory boundaries to drain; the enclosing job controller must preserve
    the actual final successful B timestamp for the public JOB_CYCLES contract.
    Keep descriptor validation and host/status/watchdog integration explicit.
    See the [scheduler contract](tile-scheduler.md).

31. **Keep overlap behind an explicit build identity and qualify both modes.**
    `ENABLE_OVERLAP=1` selects the validated job shell, tagged scheduler,
    duplex DMA and concurrent local ports. VERSION=0x200 supports MODE=0/1;
    the default VERSION=0x100 serial hierarchy remains available. Snapshot
    configuration before widened range/overlap validation and acknowledge
    START only on actual scheduler acceptance. Timestamp the last successful
    raw B handshake because scheduler completion follows it. Count only job
    traffic; freeze the first fatal code and public counters while accepted
    obligations drain. Register watchdog/fatal feedback to avoid a
    progress-to-stop-to-handshake combinational loop. Compare MODE=0/1 on
    one qualified image with identical matrix bytes; a single macrotile
    cannot demonstrate overlap. See [the integrated contract](ddr-overlap.md).

32. **Separate fault presence from the diagnostic payload.** The first full
    overlap route missed setup by 2.029 ns. Its worst path carried the stored
    error code through repeated normalization and priority selection into
    512 frozen-counter enables; scheduler address updates shared the chain.
    Detect the fault as a scalar from the same conditions and use that signal
    for admission, stopping and snapshots. Keep priority and normalized codes
    in the error payload, with no added clock cycle. Assert scalar/payload
    equivalence in simulation and rerun the physical gate. See the
    [failed route and source identity](../results/ddr_overlap/initial_timing/README.md).

33. **Include required physical optimization in the reproducible build.**
    Scalar fault detection removes the original control bottleneck, but its
    first route still misses setup by 0.237 ns at 14 endpoints. An isolated
    `AggressiveExplore` post-route probe passes with +0.043 ns setup margin.
    Enable the managed post-route physical-optimization step for the overlap
    hierarchy and generate final checkpoints/reports after that step. Keep
    intermediate routing results visible. Require the same strict timing,
    CDC, routing and constraint gates on the optimized design; do not change
    clocks or introduce timing exceptions to clear a violation. The Tcl flow
    is part of the source identity and requires fresh vendor/image qualification.
    See the [measured probe](../results/ddr_overlap/timing_predicate/postroute_physopt/README.md).

34. **Require uninterrupted host qualification.** A host READ_REG timeout during
    Windows Modern Standby stops the test; elapsed sleep time cannot extend an
    endurance result. Preserve the failed records and reconnect read-only to
    inspect the retained identity, status and counters before considering more
    work. Never replay an uncertain START. A new endurance run starts from zero;
    only intact, completed sealed units may be reused. Long Windows commands
    hold a temporary execution-state request, without changing system power
    plans. This avoids automatic idle sleep while preserving explicit user
    Sleep behavior. See [the qualification procedure](testing.md#release-board-measurements).

RTL simulation, isolated synthesis, full platform timing and board measurements
are recorded separately.
