#-----------------------------------------------------------------------------
# build.tcl
#
# Headless synthesis -> implementation -> bitstream for the Nexys A7-50T
# accelerator. Creates the project first if it is not there yet.
#
# Usage:
#   vivado -mode batch -source scripts/build.tcl
#   vivado -mode batch -source scripts/build.tcl -tclargs -jobs 8
#   vivado -mode batch -source scripts/build.tcl -tclargs -synth-only
#
# Options (after -tclargs):
#   -jobs <n>       parallel jobs                 (default: 4)
#   -name <name>    project name                  (default: nexys_accelerator)
#   -dir  <path>    project parent dir            (default: <repo>/vivado)
#   -synth-only     stop after synthesis
#   -no-bitstream   implement but skip write_bitstream
#
# Exits non-zero if any run fails or if the routed design misses timing.
#-----------------------------------------------------------------------------

set script_path [file normalize [info script]]
set script_dir  [file dirname $script_path]
set repo_root   [file normalize [file join $script_dir ".."]]

set proj_name    "nexys_accelerator"
set proj_dir     [file join $repo_root "vivado"]
set jobs         4
set synth_only   0
set do_bitstream 1

for {set i 0} {$i < [llength $argv]} {incr i} {
    set opt [lindex $argv $i]
    switch -- $opt {
        "-jobs"         { incr i ; set jobs [lindex $argv $i] }
        "-name"         { incr i ; set proj_name [lindex $argv $i] }
        "-dir"          { incr i ; set proj_dir [file normalize [lindex $argv $i]] }
        "-synth-only"   { set synth_only 1 }
        "-no-bitstream" { set do_bitstream 0 }
        default         { return -code error "build.tcl: unknown option '$opt'" }
    }
}

set proj_path [file join $proj_dir $proj_name]
set xpr       [file join $proj_path "$proj_name.xpr"]

#-----------------------------------------------------------------------------
# Open, or create on first run
#-----------------------------------------------------------------------------
if {[file exists $xpr]} {
    puts "INFO: opening $xpr"
    open_project $xpr
} else {
    puts "INFO: no project at $xpr, creating it"
    set argv [list -name $proj_name -dir $proj_dir]
    source [file join $script_dir "create_project.tcl"]
}

if {[get_property top [get_filesets sources_1]] eq ""} {
    return -code error "No top module. Add RTL under src/hdl/ before building."
}

#-----------------------------------------------------------------------------
# Helper: launch a run and abort with a useful message if it fails
#-----------------------------------------------------------------------------
proc run_step {run_name jobs} {
    puts "INFO: launching $run_name on $jobs job(s)"
    reset_run  $run_name
    launch_runs $run_name -jobs $jobs
    wait_on_run $run_name
    set status [get_property STATUS   [get_runs $run_name]]
    set prog   [get_property PROGRESS [get_runs $run_name]]
    if {$prog ne "100%"} {
        return -code error "$run_name failed at $prog (status: $status). See the run log under the project's .runs directory."
    }
    puts "INFO: $run_name complete"
}

#-----------------------------------------------------------------------------
# Synthesis
#-----------------------------------------------------------------------------
run_step synth_1 $jobs

open_run synth_1 -name synth_1
puts "\n----- post-synthesis utilization -----"
report_utilization -quiet
close_design

if {$synth_only} {
    puts "INFO: -synth-only given, stopping after synthesis"
    return
}

#-----------------------------------------------------------------------------
# Implementation (+ bitstream)
#-----------------------------------------------------------------------------
if {$do_bitstream} {
    set_property STEPS.WRITE_BITSTREAM.ARGS.VERBOSE false [get_runs impl_1]
    launch_runs impl_1 -to_step write_bitstream -jobs $jobs
} else {
    launch_runs impl_1 -jobs $jobs
}
wait_on_run impl_1

if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
    return -code error "impl_1 failed at [get_property PROGRESS [get_runs impl_1]] (status: [get_property STATUS [get_runs impl_1]])."
}

open_run impl_1

puts "\n----- post-route utilization -----"
report_utilization -quiet

puts "\n----- post-route timing -----"
set wns [get_property STATS.WNS [get_runs impl_1]]
set whs [get_property STATS.WHS [get_runs impl_1]]
set tns [get_property STATS.TNS [get_runs impl_1]]
puts "WNS = $wns ns    WHS = $whs ns    TNS = $tns ns"

set timing_ok 1
if {$wns ne "" && $wns < 0} { set timing_ok 0 }
if {$whs ne "" && $whs < 0} { set timing_ok 0 }

if {$do_bitstream} {
    set bit [file join $proj_path "$proj_name.runs" "impl_1" \
                       "[get_property top [get_filesets sources_1]].bit"]
    if {[file exists $bit]} {
        puts "INFO: bitstream at $bit"
    } else {
        puts "WARNING: expected bitstream not found at $bit"
    }
}

if {!$timing_ok} {
    report_timing_summary -delay_type min_max -max_paths 5 -quiet
    return -code error "TIMING NOT MET (WNS=$wns, WHS=$whs). Bitstream is not trustworthy."
}

puts "\nINFO: build succeeded, timing met."
