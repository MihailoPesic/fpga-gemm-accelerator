# Final-image board measurements

Build `0x2c680af7` ran on the connected Nexys A7-50T after an operator-reported
fresh OFF/ON with JP1 in JTAG mode. The actual JTAG program process and all board
stages exited zero. The selected bitstream SHA-256 is `5124adc4348ed1f42b6420d2776d7d1f50a44df7e340a6789b3a9954373e4cf5`.

The same P8/T32 build, four-read engine, 100 MHz core and 115200-baud transport
passed the complete 48-job plan in MODE=0 and MODE=1: 99,186 useful output
comparisons in total, including signed extremes, odd tails and multiple tiles.
Those two plans preserve the completed host validation records, counters and
console output; they did not save every raw output allocation for replay.

The controlled 64x64x256 comparison then ran 30 matched pairs, with pair order
alternating. A and BT were uploaded once; the complete C allocation was
reinitialized before every job. All 245,760 useful outputs and 3,709,440
allocation bytes matched the independently reconstructed oracle and padding.
Median FPGA job cycles: MODE=0 46163.5; MODE=1
25130. Median useful throughput: MODE=0
4.542879 GOPS; MODE=1 8.345213
GOPS. Median paired cycle ratio (serial / overlap):
1.836611; the ratio of cycle medians is
1.836988. These are measured DDR-resident jobs,
including tile loads, result writes and the final successful write response.

Host preparation, UART transfer, polling, output/allocation download and validation
times are recorded separately in the raw reports. Raw UART logging and flushing
add overhead to host wall times. Core throughput uses frozen FPGA counters,
not USB/UART elapsed time or the arithmetic peak.

`matched_benchmark/snapshots.tar.gz` contains the original initial allocations,
wide oracle and all per-job output/counter/allocation snapshots. The inventory
seals each member's original path, byte count and SHA-256; extraction is optional.
`uart.jsonl.gz` is a lossless copy of the complete byte trace. The independent
COBS/CRC/command verifier binds its 60 START/completion lifecycles, descriptors,
frozen counters and memory bytes to those snapshots. The captured trace observes
the host protocol; it cannot independently observe the final AXI B edge.

`matched_benchmark/method.py` is the byte-identical measured helper source,
preserved as evidence. To repeat this exact bounded comparison, copy it to
`build/bench_overlap_matched.py` in the matching source checkout, select the
same qualified build manifest and currently programmed image, and use a fresh
output directory. The UART verifier snapshot likewise belongs at its original
`build/verify_overlap_uart.py` path when replaying the uncompressed saved trace
and snapshots. The public host CLI and board qualification runner remain the
normal supported entry points.

This is a bounded cold-start run. It does not establish maximum-dimension board
coverage, 30-minute endurance, warm-reset reliability or DDR electrical margins.
The operator receipt is a report of physical setup, not an automatic electrical
qualification. Existing vendor and routed proof seals remain unchanged.
