# Nexys A7-50T pins from reference/Nexys-A7-50T-Master.xdc.
set_property -dict {PACKAGE_PIN E3 IOSTANDARD LVCMOS33} [get_ports CLK100MHZ]
create_clock -name core -period 10.000 [get_ports CLK100MHZ]
set_clock_uncertainty 0.200 [get_clocks core]
set_property -dict {PACKAGE_PIN C12 IOSTANDARD LVCMOS33} [get_ports CPU_RESETN]
set_property -dict {PACKAGE_PIN C4 IOSTANDARD LVCMOS33} [get_ports UART_TXD_IN]
set_property -dict {PACKAGE_PIN D4 IOSTANDARD LVCMOS33} [get_ports UART_RXD_OUT]
set_property -dict {PACKAGE_PIN H17 IOSTANDARD LVCMOS33} [get_ports {LED[0]}]
set_property -dict {PACKAGE_PIN K15 IOSTANDARD LVCMOS33} [get_ports {LED[1]}]
set_property -dict {PACKAGE_PIN J13 IOSTANDARD LVCMOS33} [get_ports {LED[2]}]
set_property -dict {PACKAGE_PIN N14 IOSTANDARD LVCMOS33} [get_ports {LED[3]}]
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]

# UART RX is asynchronous; only entry to the first synchronizer is excluded.
# Synchronizer stages and all downstream logic remain timed at 100 MHz.
set_false_path -from [get_ports UART_TXD_IN] -to [get_pins {preview/receiver/sync_reg[0]/D}]
# The button is sampled through three flops. Only the first D input is
# asynchronous; both assertion and release inside the design remain timed.
set_false_path -from [get_ports CPU_RESETN] -to [get_pins {reset_sync_reg[0]/D}]
# These outputs have no external clock relationship: LEDs and 8N1 UART.
# The UART serializer and every register feeding these ports remain timed.
set_false_path -to [get_ports {UART_RXD_OUT LED[*]}]
