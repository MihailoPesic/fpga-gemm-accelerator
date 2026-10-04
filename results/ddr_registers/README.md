# DDR register verification

October 2, 2026: all four P/T builds pass four unit tests each, 16 total.
Each build exercises 435 requests, 432 responses and three requests canceled
by reset. The suite checks all defined registers and representative invalid
16-bit addresses, every STATUS combination, partial-write masks, read-only
writes, configuration protection,
counter halves and parameterized identity/clock fields.

Deferred START acknowledgement, held requests/responses and one-cycle CLEAR
pulses are checked with independent job status/handshake inputs. Busy-at-
acceptance tests deliberately finish the old job before decoding; START,
CLEAR and configuration writes must still return BUSY without effects.
Fault counter snapshots remain readable even with BUSY asserted.
Reset checks cancel pending local transactions as part of coordinated reset.

Reproduce with:

```text
make test-ddr-registers PYTHON=.venv/bin/python
```

The [summary](summary.json) records source hashes, exact parameters and tool
versions; per-build coverage and XML are preserved unchanged. P8/T32 also
overrides ID, VERSION and CORE_HZ to verify parameter reporting. That test
clock value is a mock configuration, not a routed-clock measurement.

This is a local-bus unit test with modeled job ports. The
[production matrix path](../ddr_job/README.md) is verified separately.
UART, host-memory arbitration and board integration remain open.
The [register contract](../../docs/ddr-registers.md) explains the interface.
