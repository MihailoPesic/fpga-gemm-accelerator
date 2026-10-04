# Final overlap route gate

Build `0x2c680af7`: P8/T32, four reads outstanding, selectable
MODE=0/1, 100 MHz core. The managed post-route `AggressiveExplore` stage
completed with setup/hold slack +0.038/+0.016 ns.
All 13 final physical reports and ten SmartConnect bus-skew checks passed.
The Tcl copy differs from the production flow only by stopping before
bitstream generation, after all routing and timing gates.

The checkpoint hash and exact source/build settings are recorded in
[summary.json](summary.json). This gate does not establish vendor-model or
physical-board correctness. The final bitstream needs its own routed reports.
