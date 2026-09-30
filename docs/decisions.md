# Design decisions

The [technical specification](specification.md) defines the project target.
These decisions clarify its implementation contracts. Saved compute evidence
is in [results/core](../results/core/README.md); current integration status is in
[status.md](status.md). Changes to these decisions require matching tests.

1. **Platform baseline.** Target xc7a50ticsg324-1L. Preserve the working
   200 MHz DDR2 / 50 MHz native UI configuration for the initial transition.
   These are the saved project's settings, not a claim that 200 MHz is the
   board's maximum memory speed. Generate and verify the AXI MIG/converter
   configuration separately. Native `app_addr` values must not enter the new
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
7. **Reset and errors.** Coordinate resets across master, conversion and MIG,
   overlap reset assertion for at least 16 cycles of the slowest AXI clock,
   and deassert synchronously in each stable domain. UART status remains
   available while calibration is pending. Per-command BUSY/BAD responses must
   not corrupt an active job's error state or completion snapshot.
8. **Completion and counters.** JOB_CYCLES measures accepted START through
   the final successful B handshake at the owned AXI boundary. It does not
   timestamp physical SDRAM cell writes or expose opaque vendor queues.
   COMPUTE_CYCLES increments on each busy edge after launch, including final
   drain: K+3P-1 per completed microtile, excluding launch/prefetch. Status
   polling is not watchdog progress. Freeze all completed-job counters before
   reporting DONE. Error jobs cannot report successful completion.
9. **Performance comparisons.** T8 to T32 changes input reuse and result burst
   shape. Byte counts isolate transfer-volume savings; explain both effects
   when attributing runtime improvements. Preserve the same P/clock/shape.
   Seven useful GOPS on 256 cubed needs at least 273.4375 MB/s effective mixed
   tile traffic even with ideal overlap; it remains a measured stretch target.

RTL simulation, isolated synthesis, full platform timing and board measurements
are recorded separately.
