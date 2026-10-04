# Final overlap vendor integration

Build `0x2c680af7` passed all three complete UART jobs,
including a MODE=1 job spanning two macrotiles. All 49 outputs, memory guards,
traffic totals and final write-completion boundaries were checked.

| Job | M x N x K | MODE | Outputs | DDR-job cycles |
| --- | --- | ---: | ---: | ---: |
| 1 | 1 x 1 x 1 | 0 | 1 | 220 |
| 2 | 5 x 3 x 9 | 0 | 15 | 478 |
| 3 | 1 x 33 x 9 | 1 | 33 | 1024 |

The fixture drives actual UART pins at a simulation-only 10 Mbaud. Generated
SmartConnect, MIG and the unmodified DDR2 model are present. FAST calibration
produces the retained 200 us CKE initialization warning; ideal PCB delays and
accelerated initialization do not qualify physical DDR timing or warm resets.
The physical UART setting is 115200 baud and requires board evidence.

[summary.json](summary.json) binds the completed test to its source and generated
IP identities. [console_excerpt.txt](console_excerpt.txt) retains all warnings,
failure lines and test markers with original line numbers and full-log hash.
