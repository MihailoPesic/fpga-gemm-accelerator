# Four-read vendor integration

Build `0xd558a543`, P8/T32 with READ_SLOTS=4, passes two complete
framed-UART jobs through the production core, SmartConnect, MIG and
unchanged DDR2 model. All 16 outputs and the fixture counters match.
[Summary](summary.json) and [manifest](manifest.json) retain the exact
source, generated-platform and copied-evidence identities.

The native run exits zero and closes normally after the final PASS.
Simulation uses 10 Mbaud, FAST calibration and ideal PCB delays. The
physical image uses 115200 baud. All [warning and result lines](console_excerpt.txt)
are retained. This is functional integration evidence; it supplies
no physical-board timing, correctness or throughput measurement.
