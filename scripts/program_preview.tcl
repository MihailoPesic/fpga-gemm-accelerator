if {[llength $argv] != 1 || ![file isfile [lindex $argv 0]]} {
    error "Use python scripts/program_preview.py --manifest build/preview/build.json"
}
open_hw_manager
connect_hw_server -url localhost:3121
set targets [get_hw_targets]
if {[llength $targets] != 1} { error "Expected one connected JTAG target; found $targets" }
current_hw_target [lindex $targets 0]
open_hw_target
set devices [get_hw_devices]
if {[llength $devices] != 1} { error "Expected one FPGA; found $devices" }
set device [lindex $devices 0]
if {![string match xc7a50t* [get_property PART $device]]} {
    error "Connected device is not xc7a50t: [get_property PART $device]"
}
current_hw_device $device
set_property PROGRAM.FILE [file normalize [lindex $argv 0]] $device
set_property PROBES.FILE {} $device
set_property FULL_PROBES.FILE {} $device
program_hw_devices $device
refresh_hw_device -update_hw_probes false $device
puts "PREVIEW_PROGRAMMED [get_property PART $device] [get_property PROGRAM.FILE $device]"
close_hw_target
disconnect_hw_server
close_hw_manager
