# Historical board tools

`nexys.py` speaks the July 2026 native-MIG protocol at 921600 baud: big-endian
fields and MIG `app_addr` units, not byte addresses. It matches the original
`top.sv` and `uart_ddr_bridge.sv` under `accelerator nexys.srcs/`.
Run `python host/legacy/nexys.py COMx` with pyserial installed and the matching
native baseline bitstream. Inspect its CLI before use on another platform.

This driver does not implement the new COBS/CRC, little-endian GEMM protocol.
A one-byte DDR-not-ready
reply to READ is currently reported as a short-read timeout by this driver;
check the calibration LED before testing memory.
