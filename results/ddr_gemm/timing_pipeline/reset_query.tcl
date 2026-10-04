set_param general.maxThreads 1
if {[llength $argv] != 1} { error "Expected copied frozen routed checkpoint" }
open_checkpoint [lindex $argv 0]
set fp [open reset_selector.txt w]
set nets [get_nets -hier -filter {NAME =~ */u_iodelay_ctrl/sys_rst_i}]
puts $fp "Exact generated MIG reset selector matches: [llength $nets]"
foreach net $nets {
    puts $fp "NET [get_property NAME $net]"
    foreach pin [all_fanout -flat -endpoints_only -from $net] { puts $fp "ENDPOINT [get_property NAME $pin]" }
}
puts $fp "All hierarchical sys_rst name matches:"
foreach net [get_nets -hier -filter {NAME =~ *sys_rst*}] { puts $fp [get_property NAME $net] }
close $fp
puts "RESET_SELECTOR_REVIEW_COMPLETE_NO_DESIGN_EDITS"
close_design
