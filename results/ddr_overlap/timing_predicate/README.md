# Scalar fault-predicate revision

The scheduler and job shell now use a scalar fault-presence signal for
stop, readiness and completion decisions. Error-code selection retains its
original priority and normalization. No register or cycle was added.
Simulation assertions check agreement between the scalar predicate and the
diagnostic code.

The [record](record.json) seals actual process exits, XML results, coverage
and source hashes for 154 passing tests: 1,056 completed jobs and 322,799
compared output values. The job-shell suites include 800 seeded jobs across
P=4/8, T=8/32 and one/four read slots. Packet tests compare both modes with
identical inputs, delays, guard regions and final-write-response checks.
The [software checks](unit_checks/record.json) pass 77 tests on each of
Windows and Linux.

The [route](initial_timing/README.md) for build `0x36ffc81c` fits the board,
but misses setup by 0.237 ns. A subsequent
[post-route optimization probe](postroute_physopt/README.md) passes with
setup/hold margins +0.043/+0.020 ns. It has no qualified bitstream or board result.
The production flow includes that optimization as a managed implementation
stage. Its [fresh source-matched route](final_build/route_probe/README.md),
build `0x2c680af7`, passes all physical gates with setup/hold margins
+0.038/+0.016 ns. The [vendor integration](final_build/vendor/README.md)
passes three complete framed-UART jobs and all 49 outputs with a normal exit.
The [production bitstream and own-checkpoint review](final_build/routed/README.md)
are complete for that exact build. The [cold-start board record](final_build/board/README.md)
passes 48 jobs per mode and 30 matched pairs: 156 jobs and 344,946 outputs.
The 64x64x256 comparison measures 8.345 useful GOPS with overlap and a 1.837x
median paired speedup. Maximum-size, endurance and warm-reset qualification
remain separate gates.
Earlier [integration](../record.json) and
[timing](../initial_timing/README.md) records retain their original source
identities.
