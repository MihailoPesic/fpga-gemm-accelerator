# Board diagnostic I/O. axi_ddr.xdc and generated IP own clocks and DDR pins.
set_property -dict {PACKAGE_PIN C4 IOSTANDARD LVCMOS33} [get_ports UART_TXD_IN]
set_property -dict {PACKAGE_PIN D4 IOSTANDARD LVCMOS33} [get_ports UART_RXD_OUT]
set_property -dict {PACKAGE_PIN H17 IOSTANDARD LVCMOS33} [get_ports {LED[0]}]
set_property -dict {PACKAGE_PIN K15 IOSTANDARD LVCMOS33} [get_ports {LED[1]}]
set_property -dict {PACKAGE_PIN J13 IOSTANDARD LVCMOS33} [get_ports {LED[2]}]
set_property -dict {PACKAGE_PIN N14 IOSTANDARD LVCMOS33} [get_ports {LED[3]}]
# UART is asynchronous to the FPGA. Only its synchronizer entry is excluded.
set_false_path -from [get_ports UART_TXD_IN] -to [get_pins {receiver/sync_reg[0]/D}]
# LEDs and the UART output have no receiving clock supplied by the board.
set_false_path -to [get_ports {UART_RXD_OUT LED[*]}]
