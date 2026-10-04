set probe_out {C:/Users/mihai/Documents/continium/accelerator nexys/build/overlap_fault_physopt_probe}
open_checkpoint {C:/Users/mihai/Documents/continium/accelerator nexys/build/overlap_fault_route_probe/gemm_routed.dcp}
phys_opt_design -directive AggressiveExplore
write_checkpoint -force [file join $probe_out post_physopt.dcp]
foreach {command filename} {
    report_utilization utilization.txt report_clocks clocks.txt
    report_clock_interaction clock_interaction.txt report_clock_utilization clock_utilization.txt
    report_route_status route.txt report_drc drc.txt report_methodology methodology.txt
    report_control_sets control_sets.txt
} { $command -file [file join $probe_out $filename] }
report_timing_summary -delay_type min_max -report_unconstrained -max_paths 10 -file [file join $probe_out timing.txt]
check_timing -verbose -file [file join $probe_out check_timing.txt]
report_cdc -name probe_cdc -details -show_waiver -file [file join $probe_out cdc.txt]
set cdc_critical [get_cdc_violations -name probe_cdc -filter {SEVERITY == "Critical"}]
set pulse_before [get_msg_config -count -severity {CRITICAL WARNING}]
report_pulse_width -warn_on_violation -file [file join $probe_out pulse_width.txt]
set pulse_after [get_msg_config -count -severity {CRITICAL WARNING}]
set pulse_violations [report_pulse_width -all_violators -return_string]
set fp [open [file join $probe_out pulse_width_violations.txt] w]
puts $fp $pulse_violations
close $fp
set skew_before [get_msg_config -count -severity {CRITICAL WARNING}]
report_bus_skew -delay_type min_max -warn_on_violation -file [file join $probe_out bus_skew.txt]
set skew_after [get_msg_config -count -severity {CRITICAL WARNING}]
set fp [open [file join $probe_out bus_skew.txt] r]
set skew_report [read $fp]
close $fp
set routed [report_route_status -boolean_check ROUTED_FULLY]
set route_errors [report_route_status -boolean_check ERRORS_IN_ROUTES]
set setup [get_timing_paths -delay_type max -max_paths 1]
set hold [get_timing_paths -delay_type min -max_paths 1]
if {![llength $setup] || ![llength $hold]} { error "No timing paths in optimized checkpoint" }
set wns [get_property SLACK $setup]
set whs [get_property SLACK $hold]
set critical [concat [get_drc_violations -quiet -filter {SEVERITY == "Error" || SEVERITY == "Critical Warning"}]     [get_methodology_violations -quiet -filter {SEVERITY == "Error" || SEVERITY == "Critical Warning"}]]
puts "PHYSOPT_PROBE_RESULT WNS=$wns WHS=$whs ROUTED=$routed ROUTE_ERRORS=$route_errors CRITICAL_CDC=[llength $cdc_critical] CRITICAL_CHECKS=[llength $critical]"
if {[llength $cdc_critical]} { error "Critical CDC paths remain" }
if {$pulse_after > $pulse_before || [regexp -line {^\s*(Min Period|Max Period|Low Pulse Width|High Pulse Width|Max Skew|Min Skew)\s+} $pulse_violations]} {
    error "Pulse-width/period/skew checks failed"
}
if {$skew_after > $skew_before || [regexp {Slack\s+\(VIOLATED\)} $skew_report] || ![regexp {Slack\s+\(MET\)} $skew_report]} {
    error "Bus-skew checks failed or missing"
}
if {!$routed || $route_errors || [llength $critical]} { error "Routing or critical physical checks failed" }
set fp [open [file join $probe_out check_timing.txt] r]
set checks [read $fp]
close $fp
foreach category {no_clock constant_clock unconstrained_internal_endpoints loops latch_loops} {
    if {![regexp [format {(?m)^[0-9]+\. checking %s \(0\)\r?$} $category] $checks]} {
        error "check_timing: $category is not zero"
    }
}
if {$wns < 0 || $whs < 0} { error "Timing failed WNS=$wns WHS=$whs" }
puts "PHYSOPT_PROBE_PASS"
close_design
