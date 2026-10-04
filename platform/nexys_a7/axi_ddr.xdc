# Nexys A7-50T platform pins. MIG owns all DDR pin and PHY constraints.
# The clock wizard owns the 10 ns primary clock on CLK100MHZ and its generated
# clocks; do not also load preview.xdc or create another primary clock here.
set_property -dict {PACKAGE_PIN E3 IOSTANDARD LVCMOS33} [get_ports CLK100MHZ]
set_property -dict {PACKAGE_PIN C12 IOSTANDARD LVCMOS33} [get_ports CPU_RESETN]
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]

# Reset synchronizers and calibration CDC are vendor IP with scoped XDC.
# Do not mask whole clock domains or all paths through reset logic here.
# A board top adds its UART/LED pins and narrow asynchronous-I/O exceptions.
