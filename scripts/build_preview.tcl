# Called by build_preview.py with a generated source/identity configuration.
if {[llength $argv] != 1} { error "Use python scripts/build_preview.py" }
source [lindex $argv 0]
cd $preview_out
set_param general.maxThreads 1
create_project -in_memory -part xc7a50ticsg324-1L
read_verilog -sv $preview_sources
read_xdc [list $preview_xdc]
synth_design -top gemm_preview_top -part xc7a50ticsg324-1L \
    -generic [list BAUD=$preview_baud BUILD_ID=$preview_build_id]
set dsp [llength [get_cells -hier -filter {REF_NAME == DSP48E1}]]
set bufg [llength [get_cells -hier -filter {REF_NAME == BUFG}]]
if {$dsp != 16 || $bufg != 1} { error "Expected 16 DSPs and one BUFG; got $dsp / $bufg" }
if {[llength [get_clocks]] != 1} { error "Expected one 100 MHz clock" }
foreach pin {preview/receiver/sync_reg[0]/D reset_sync_reg[0]/D} {
    if {[llength [get_pins -quiet $pin]] != 1} { error "Missing constrained synchronizer pin: $pin" }
}
opt_design
place_design
route_design
write_checkpoint -force preview_routed.dcp
report_utilization -file utilization.txt
report_timing_summary -delay_type min_max -report_unconstrained -max_paths 5 -file timing.txt
report_timing -delay_type min -path_type full_clock_expanded -max_paths 3 -file hold_paths.txt
report_clocks -file clocks.txt
report_clock_utilization -file clock_utilization.txt
report_route_status -file route.txt
set fully_routed [report_route_status -boolean_check ROUTED_FULLY]
set route_errors [report_route_status -boolean_check ERRORS_IN_ROUTES]
if {!$fully_routed || $route_errors} { error "Routing incomplete or erroneous" }
report_pulse_width -warn_on_violation -file pulse_width.txt
report_cdc -details -file cdc.txt
report_drc -file drc.txt
report_methodology -file methodology.txt
check_timing -verbose -file check_timing.txt
set wns [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set whs [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
if {$wns eq "" || $whs eq "" || $wns < 0 || $whs < 0} { error "Timing failed: WNS=$wns WHS=$whs" }
set critical [concat \
    [get_drc_violations -quiet -filter {SEVERITY == "Error" || SEVERITY == "Critical Warning"}] \
    [get_methodology_violations -quiet -filter {SEVERITY == "Error" || SEVERITY == "Critical Warning"}]]
if {[llength $critical]} { error "Unresolved critical violations: $critical" }
set critical_count [get_msg_config -count -severity {CRITICAL WARNING}]
if {![string is integer -strict $critical_count] || $critical_count != 0} {
    error "Unresolved critical warnings: $critical_count"
}
# Fail if constraints silently miss an internal clock/path. Asynchronous board
# ports are narrowly excluded by preview.xdc and documented there.
set fp [open check_timing.txt r]
set checks [read $fp]
close $fp
foreach category {no_clock constant_clock unconstrained_internal_endpoints loops latch_loops} {
    set pattern [format {(?m)^[0-9]+\. checking %s \(0\)\r?$} $category]
    if {![regexp $pattern $checks]} { error "check_timing: $category is not zero" }
}
write_bitstream -force gemm_preview.bit
set fp [open timing_pass.json w]
puts $fp "{\"vivado\":\"[version -short]\",\"wns_ns\":$wns,\"whs_ns\":$whs,\"dsp\":$dsp,\"clock_hz\":100000000,\"clock_uncertainty_ns\":0.2}"
close $fp
puts "PREVIEW_BUILD_PASS WNS=$wns WHS=$whs DSP=$dsp"
