# Build the existing native-DDR UART baseline. Full GEMM board top is pending.
set repo_root [file normalize [file join [file dirname [info script]] ..]]
set synth_only 0
set write_bit 1
foreach arg $argv {
    switch -- $arg {
        -synth-only { set synth_only 1 }
        -no-bitstream { set write_bit 0 }
        default { error "Supported flags: -synth-only, -no-bitstream" }
    }
}
set baseline_dir [file join $repo_root build native_ddr]
set project_file [file join $baseline_dir native_ddr.xpr]
if {[file exists $project_file]} {
    open_project $project_file
} else {
    set argv [list]
    source [file join $repo_root scripts create_project.tcl]
}
source [file join $repo_root platform nexys_a7 board.tcl]
foreach ip [get_ips] {
    if {[get_property IS_LOCKED $ip]} { error "Locked IP $ip; inspect report_ip_status before building" }
}
report_ip_status -file [file join $baseline_dir ip_status.txt]
generate_target all [get_ips]
reset_run synth_1
launch_runs synth_1 -jobs 4
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} { error "Native baseline synthesis failed" }
open_run synth_1
report_utilization -file [file join $baseline_dir synthesis.txt]
set roots [get_clocks -of_objects [get_ports CLK100MHZ]]
if {[llength $roots] != 1} { error "Expected one CLK100MHZ source clock, found $roots" }
report_clocks -file [file join $baseline_dir clocks.txt]
if {$synth_only} { close_project; return }
close_design
reset_run impl_1
launch_runs impl_1 -to_step route_design -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { error "Native baseline implementation failed" }
open_run impl_1
report_timing_summary -delay_type min_max -report_unconstrained -file [file join $baseline_dir timing.txt]
report_methodology -file [file join $baseline_dir methodology.txt]
report_drc -file [file join $baseline_dir drc.txt]
foreach type {max min} {
    set paths [get_timing_paths -delay_type $type -max_paths 1]
    if {[llength $paths] == 0 || [get_property SLACK $paths] < 0} { error "Native baseline timing fails ($type)" }
}
if {$write_bit} {
    # Positive WNS alone is not a board release gate. Do not quietly emit an
    # accepted bitstream with unresolved methodology/DRC critical warnings.
    set critical [concat \
        [get_drc_violations -quiet -filter {SEVERITY == "Error" || SEVERITY == "Critical Warning"}] \
        [get_methodology_violations -quiet -filter {SEVERITY == "Error" || SEVERITY == "Critical Warning"}]]
    if {[llength $critical]} { error "Review critical violations before writing a baseline bitstream: $critical" }
    write_bitstream -force [file join $baseline_dir native_ddr.bit]
}
close_project
