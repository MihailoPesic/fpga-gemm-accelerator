if {[llength $argv] != 1} { error "Use python scripts/build_ddr_diag.py" }
source [lindex $argv 0]
set_param general.maxThreads 1
set project_file [file join $ddr_out project axi_platform.xpr]
if {[file exists $project_file]} {
    open_project $project_file
    if {[get_property PART [current_project]] ne "xc7a50ticsg324-1L"} { error "Unexpected FPGA part" }
} else {
    source [file join $ddr_root scripts create_axi_platform.tcl]
}
foreach src $ddr_sources {
    if {![llength [get_files -quiet [list $src]]]} { add_files -norecurse [list $src] }
}
set board_xdc [file join $ddr_root platform nexys_a7 ddr_diag.xdc]
if {![llength [get_files -quiet [list $board_xdc]]]} { add_files -fileset constrs_1 -norecurse [list $board_xdc] }
set_property top gemm_ddr_diag_top [get_filesets sources_1]
set_property generic [list BUILD_ID=$ddr_build_id BAUD=115200] [get_filesets sources_1]
update_compile_order -fileset sources_1

if {$ddr_stage eq "sim"} {
    set mig [get_ips -all -filter {IPDEF == xilinx.com:ip:mig_7series:4.2}]
    if {[llength $mig] != 1} { error "Expected one MIG IP" }
    # MIG's full BD generation already emits its example DDR model. Nested
    # IP targets must be generated through the parent block design.
    set models [glob -nocomplain [file join $ddr_out project axi_platform.gen sources_1 bd * ip * * example_design sim ddr2_model.v]]
    if {[llength $models] != 1} { error "Expected one generated DDR2 model; found $models" }
    set imports [file join $ddr_out project imports]
    file mkdir $imports
    foreach name {ddr2_model.v ddr2_model_parameters.vh} {
        set dst [file join $imports $name]
        file copy -force [file join [file dirname [lindex $models 0]] $name] $dst
        if {![llength [get_files -quiet [list $dst]]]} { add_files -fileset sim_1 -norecurse [list $dst] }
    }
    set bench [file join $ddr_root tb vendor tb_ddr_platform.sv]
    if {![llength [get_files -quiet [list $bench]]]} { add_files -fileset sim_1 -norecurse [list $bench] }
    set_property top tb_ddr_platform [get_filesets sim_1]
    set_property generic [list WARM_RESET=$ddr_warm_reset] [get_filesets sim_1]
    set_property xsim.simulate.runtime 0ns [get_filesets sim_1]
    update_compile_order -fileset sim_1
    launch_simulation
    run all
    close_sim
    set fp [open [file join $ddr_out simulation_complete.txt] w]
    puts $fp [version]
    close $fp
    close_project
    return
}

# Global synthesis keeps the generated platform and its caller in one run.
set bd_file [get_files -all */axi_ddr_bd.bd]
set_property synth_checkpoint_mode None $bd_file
generate_target all $bd_file
reset_run synth_1
launch_runs synth_1 -jobs 1
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} { error "DDR diagnostic synthesis failed" }
launch_runs impl_1 -to_step route_design -jobs 1
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { error "DDR diagnostic implementation failed" }
open_run impl_1
write_checkpoint -force [file join $ddr_out diagnostic_routed.dcp]
foreach {command filename} {
    report_utilization utilization.txt report_clocks clocks.txt
    report_clock_interaction clock_interaction.txt report_clock_utilization clock_utilization.txt
    report_route_status route.txt report_drc drc.txt report_methodology methodology.txt
} { $command -file [file join $ddr_out $filename] }
report_timing_summary -delay_type min_max -report_unconstrained -max_paths 5 -file [file join $ddr_out timing.txt]
check_timing -verbose -file [file join $ddr_out check_timing.txt]
report_cdc -name ddr_cdc -details -show_waiver -file [file join $ddr_out cdc.txt]
# CDC report severity is independent of the message manager. Inspect its
# objects directly; any future vendor waiver must name the actual endpoints.
set cdc_critical [get_cdc_violations -name ddr_cdc -filter {SEVERITY == "Critical"}]
if {[llength $cdc_critical]} { error "Unreviewed critical CDC paths: $cdc_critical" }

# -all_violators covers pulse, period and skew checks across every clock.
# A negative slack rounded to -0.000 is also caught by warn_on_violation.
set pulse_before [get_msg_config -count -severity {CRITICAL WARNING}]
report_pulse_width -warn_on_violation -file [file join $ddr_out pulse_width.txt]
set pulse_after [get_msg_config -count -severity {CRITICAL WARNING}]
set pulse_violations [report_pulse_width -all_violators -return_string]
set pulse_fp [open [file join $ddr_out pulse_width_violations.txt] w]
puts $pulse_fp $pulse_violations
close $pulse_fp
if {![string is integer -strict $pulse_before] || ![string is integer -strict $pulse_after] ||
    $pulse_after > $pulse_before ||
    [regexp -line {^\s*(Min Period|Max Period|Low Pulse Width|High Pulse Width|Max Skew|Min Skew)\s+} $pulse_violations]} {
    error "Pulse-width, period or skew checks failed; see pulse_width.txt"
}
# SmartConnect's asynchronous FIFO pointers carry generated bus-skew
# constraints. They require their own report; setup/hold summary excludes them.
set bus_skew_before [get_msg_config -count -severity {CRITICAL WARNING}]
report_bus_skew -delay_type min_max -warn_on_violation -file [file join $ddr_out bus_skew.txt]
set bus_skew_after [get_msg_config -count -severity {CRITICAL WARNING}]
set bus_skew_fp [open [file join $ddr_out bus_skew.txt] r]
set bus_skew_report [read $bus_skew_fp]
close $bus_skew_fp
if {![string is integer -strict $bus_skew_before] || ![string is integer -strict $bus_skew_after] ||
    $bus_skew_after > $bus_skew_before ||
    [regexp {Slack\s+\(VIOLATED\)} $bus_skew_report] ||
    ![regexp {Slack\s+\(MET\)} $bus_skew_report]} {
    error "Missing or failing SmartConnect bus-skew checks; see bus_skew.txt"
}
if {[llength [get_clocks -of_objects [get_ports CLK100MHZ]]] != 1} { error "Missing/duplicate primary input clock" }
if {![report_route_status -boolean_check ROUTED_FULLY] || [report_route_status -boolean_check ERRORS_IN_ROUTES]} {
    error "Incomplete or erroneous routing"
}
set setup [get_timing_paths -delay_type max -max_paths 1]
set hold [get_timing_paths -delay_type min -max_paths 1]
if {![llength $setup] || ![llength $hold]} { error "No timing paths" }
set wns [get_property SLACK $setup]
set whs [get_property SLACK $hold]
if {$wns < 0 || $whs < 0} { error "Timing failed WNS=$wns WHS=$whs" }
set critical [concat [get_drc_violations -quiet -filter {SEVERITY == "Error" || SEVERITY == "Critical Warning"}] \
    [get_methodology_violations -quiet -filter {SEVERITY == "Error" || SEVERITY == "Critical Warning"}]]
if {[llength $critical]} { error "Unresolved critical violations: $critical" }
set fp [open [file join $ddr_out check_timing.txt] r]
set checks [read $fp]
close $fp
foreach category {no_clock constant_clock unconstrained_internal_endpoints loops latch_loops} {
    if {![regexp [format {(?m)^[0-9]+\. checking %s \(0\)\r?$} $category] $checks]} {
        error "check_timing: $category is not zero"
    }
}
write_bitstream -force [file join $ddr_out gemm_ddr_diag.bit]
set fp [open [file join $ddr_out timing_pass.json] w]
puts $fp "{\"vivado\":\"[version -short]\",\"wns_ns\":$wns,\"whs_ns\":$whs,\"core_hz\":100000000}"
close $fp
puts "DDR_DIAG_BUILD_PASS WNS=$wns WHS=$whs"
close_project
