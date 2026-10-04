set_param general.maxThreads 1
if {[llength $argv] != 1} { error "Expected copied production routed checkpoint" }
open_checkpoint [lindex $argv 0]
set fp [open reset_selector.txt w]
puts $fp "Tool: [version -short]"
set cpu [lsort [get_property NAME [all_fanout -flat -endpoints_only -from [get_ports CPU_RESETN]]]]
puts $fp "CPU_RESETN leaf endpoints: [llength $cpu]"
set pre 0
set pll 0
set unexpected 0
foreach pin $cpu {
    puts $fp "CPU_ENDPOINT $pin"
    if {[string match {platform/platform/axi_ddr_bd_i/mig/*/PRE} $pin]} {
        incr pre
    } elseif {[string match {platform/platform/axi_ddr_bd_i/mig/*/plle2_i/RST} $pin]} {
        incr pll
    } else {
        incr unexpected
        puts $fp "UNEXPECTED_ENDPOINT $pin"
    }
}
set nets [get_nets -hier -filter {NAME =~ */u_iodelay_ctrl/sys_rst_i}]
puts $fp "Exact generated MIG reset selector matches: [llength $nets]"
set selected {}
foreach net $nets {
    puts $fp "NET [get_property NAME $net]"
    foreach pin [all_fanout -flat -endpoints_only -from $net] {
        set name [get_property NAME $pin]
        lappend selected $name
        puts $fp "SELECTOR_ENDPOINT $name"
    }
}
puts $fp "MIG_PRE=$pre MIG_PLL_RST=$pll UNEXPECTED=$unexpected"
set same [expr {$cpu eq [lsort -unique $selected]}]
puts $fp "Selector endpoint set equals CPU_RESETN endpoint set: $same"
close $fp
report_exceptions -file exceptions.txt
if {[llength $nets] != 1 || $pre != 43 || $pll != 1 || $unexpected || !$same} {
    error "Production reset endpoints differ from the reviewed bounded scope"
}
puts "PRODUCTION_RESET_REVIEW_PASS_NO_DESIGN_EDITS"
close_design
