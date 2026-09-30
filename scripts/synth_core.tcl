# Standalone compute feasibility check, NOT board timing closure.
# vivado -mode batch -source scripts/synth_core.tcl
set root [file normalize [file join [file dirname [info script]] ..]]
set out [file join $root build synth_core]
file mkdir $out
cd $out
set_param general.maxThreads 4
set files [list]
foreach name {gemm_pe.sv gemm_array.sv gemm_microtile.sv} {
    lappend files [file join $root rtl core $name]
}
lappend files [file join $root tb synth gemm_core_timing_top.sv]
set records [list]
foreach p {4 8} {
    create_project -in_memory -part xc7a50ticsg324-1L
    read_verilog -sv $files
    synth_design -top gemm_core_timing_top -mode out_of_context -part xc7a50ticsg324-1L -generic P=$p
    create_clock -name core -period 10 [get_ports clk]
    # Measure register-to-register core timing on a shared clock, including
    # modeled neighbor registers. Physical board I/O is outside this harness.
    set_clock_uncertainty 0.200 [get_clocks core]
    set_false_path -from [get_ports -filter {DIRECTION == IN && NAME != clk}]
    set_false_path -to [all_outputs]
    set_property HD.CLK_SRC BUFGCTRL_X0Y0 [get_ports clk]
    set dsps [llength [get_cells -hier -filter {REF_NAME == DSP48E1}]]
    if {$dsps != $p*$p} { error "P$p used $dsps DSPs; expected [expr {$p*$p}]" }
    report_utilization -file p${p}_synth.txt
    opt_design
    place_design
    route_design
    report_utilization -file p${p}_utilization.txt
    report_utilization -hierarchical -file p${p}_hierarchy.txt
    report_timing_summary -delay_type min_max -report_unconstrained -max_paths 5 -file p${p}_timing.txt
    report_methodology -file p${p}_methodology.txt
    check_timing -verbose -file p${p}_check_timing.txt
    set setup_paths [get_timing_paths -delay_type max -max_paths 1]
    set hold_paths [get_timing_paths -delay_type min -max_paths 1]
    if {[llength $setup_paths] == 0 || [llength $hold_paths] == 0} { error "Missing timing paths" }
    set wns [get_property SLACK $setup_paths]
    set whs [get_property SLACK $hold_paths]
    if {$wns < 0 || $whs < 0} { error "P$p fails timing: WNS=$wns WHS=$whs" }
    lappend records "P=$p DSP=$dsps WNS=$wns WHS=$whs"
    puts "GEMM_CORE_PASS [lindex $records end]"
    close_project
}
set fp [open summary.txt w]
puts $fp "Vivado [version -short]; xc7a50ticsg324-1L; standalone core, 100 MHz"
puts $fp "Registered neighbor harness; external harness pin paths excluded; all register paths timed"
puts $fp "Uncertainty 0.2 ns; HD.CLK_SRC BUFGCTRL_X0Y0; NOT full board timing closure"
puts $fp [join $records \n]
close $fp
