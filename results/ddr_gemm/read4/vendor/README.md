# Four-read vendor integration

PASS in Vivado 2026.1 for P8/T32, `READ_SLOTS=4`, build `0xbb3e8247`.
The production UART, packet/control path, DMA, banked array, SmartConnect,
MIG and unchanged DDR2 model execute two complete jobs. Every result byte
and output guard is compared; counters freeze at the final successful B edge.

| M/N/K | Outputs checked | Job cycles | Compute cycles | R/W beats | Useful write bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1/1/1 | 1 | 216 | 24 | 2/1 | 4 |
| 5/3/9 | 15 | 476 | 32 | 16/10 | 60 |

The sequence also checks the bitstream identity, register geometry, CRC
reference vector, duplicate-request handling, invalid-descriptor rejection,
4 KiB splitting and transfers in the upper half of a 128-bit MIG word.
Including host commands, 65 framed exchanges accept 37 read beats, 48 write
beats and 26 write responses. These are model results, not board measurements.

UART runs at 10 Mbaud in simulation; the physical build remains 115200 baud.
Calibration uses the vendor FAST setting, with no physical PCB delays. The
retained early-CKE startup warning therefore does not qualify physical startup
timing. Generated MIG width/scalar/debug warnings are preserved. Vivado also
reports a signed-decimal overflow for BUILD_ID; its unsigned 32-bit pattern
is checked exactly through the register interface. There are no reported
errors or critical warnings, and the simulator closes normally with exit 0.

[Summary](summary.json) preserves the original runner output,
[inputs](inputs.json) records the generated platform settings, and
[console excerpt](console_excerpt.txt) retains every warning and failure line
plus completion markers. [Manifest](manifest.json) seals those files and all
current build-source hashes. Physical timing, UART and DDR validation require
separate routed and board qualification; warm reset remains unqualified.
