# Registered-neighbor timing check for the complete local engine, T=32.
set root [file normalize [file join [file dirname [info script]] ..]]
set out [file join $root build synth_tile]
file mkdir $out
cd $out
set_param general.maxThreads 1
set sources [concat [glob [file join $root rtl core *.sv]] \
    [glob [file join $root rtl memory *.sv]] [glob [file join $root rtl control *.sv]]]
lappend sources [file join $root tb synth gemm_tile_timing_top.sv]
set records [list]
foreach p {4 8} {
    create_project -in_memory -part xc7a50ticsg324-1L
    read_verilog -sv $sources
    synth_design -top gemm_tile_timing_top -mode out_of_context -part xc7a50ticsg324-1L -generic [list P=$p T=32]
    create_clock -name core -period 10 [get_ports clk]
    set_clock_uncertainty 0.200 [get_clocks core]
    set_false_path -from [get_ports -filter {DIRECTION == IN && NAME != clk}]
    set_false_path -to [all_outputs]
    set_property HD.CLK_SRC BUFGCTRL_X0Y0 [get_ports clk]
    set dsp [llength [get_cells -quiet -hier -filter {REF_NAME == DSP48E1}]]
    set ram36 [llength [get_cells -quiet -hier -filter {REF_NAME == RAMB36E1}]]
    set ram18 [llength [get_cells -quiet -hier -filter {REF_NAME == RAMB18E1}]]
    if {$dsp != $p*$p || $ram36 + $ram18 == 0} { error "Unexpected resource mapping" }
    opt_design
    place_design
    route_design
    write_checkpoint -force p${p}_routed.dcp
    if {[get_property SLACK [get_timing_paths -delay_type min -max_paths 1]] < 0} {
        phys_opt_design -hold_fix
        route_design
    }
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
    lappend records "P=$p T=32 DSP=$dsp RAMB36=$ram36 RAMB18=$ram18 WNS=$wns WHS=$whs"
    if {$wns < 0 || $whs < 0} { error "P$p misses 100 MHz: WNS=$wns WHS=$whs" }
    puts "GEMM_TILE_PASS [lindex $records end]"
    close_project
}
set fp [open summary.txt w]
puts $fp "Vivado [version -short]; xc7a50ticsg324-1L; registered neighbors; 100 MHz, uncertainty 0.2 ns"
puts $fp "External harness pin paths excluded. All engine/neighbor register paths timed. Not board timing."
puts $fp [join $records \n]
close $fp
