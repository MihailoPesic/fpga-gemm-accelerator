#-----------------------------------------------------------------------------
# create_project.tcl
#
# Regenerates the Vivado project for the Nexys A7-50T accelerator from the
# sources tracked in this repository. The .xpr is a build artifact and is NOT
# under version control -- this script is the single source of truth for the
# project's configuration.
#
# Usage:
#   vivado -mode batch -source scripts/create_project.tcl
#   vivado -mode batch -source scripts/create_project.tcl -tclargs -force
#   vivado -mode gui   -source scripts/create_project.tcl
#
# Options (after -tclargs):
#   -force          delete an existing project directory instead of aborting
#   -name <name>    project name          (default: nexys_accelerator)
#   -dir  <path>    project parent dir    (default: <repo>/vivado)
#-----------------------------------------------------------------------------

set part_name  "xc7a50ticsg324-1L"
set board_part "digilentinc.com:nexys-a7-50t:part0:1.3"
set board_xhub "digilentinc.com:xilinx_board_store:nexys-a7-50t:1.3"

# Language used for generated wrappers and instantiation templates.
# Change to VHDL if the design is predominantly VHDL.
set target_language   "Verilog"
set simulator_lang    "Mixed"

# Set to a module name to pin the top explicitly; empty means let Vivado infer.
set explicit_top ""

#-----------------------------------------------------------------------------
# Locate the repository regardless of where Vivado was invoked from
#-----------------------------------------------------------------------------
set script_path [file normalize [info script]]
set script_dir  [file dirname $script_path]
set repo_root   [file normalize [file join $script_dir ".."]]

#-----------------------------------------------------------------------------
# Arguments
#-----------------------------------------------------------------------------
set proj_name "nexys_accelerator"
set proj_dir  [file join $repo_root "vivado"]
set force     0

for {set i 0} {$i < [llength $argv]} {incr i} {
    set opt [lindex $argv $i]
    switch -- $opt {
        "-force" { set force 1 }
        "-name"  { incr i ; set proj_name [lindex $argv $i] }
        "-dir"   { incr i ; set proj_dir [file normalize [lindex $argv $i]] }
        default  { return -code error "create_project.tcl: unknown option '$opt'" }
    }
}

set proj_path [file join $proj_dir $proj_name]

puts "INFO: repository root : $repo_root"
puts "INFO: project         : $proj_name"
puts "INFO: project path    : $proj_path"

#-----------------------------------------------------------------------------
# Refuse to clobber an existing project unless -force was given
#-----------------------------------------------------------------------------
if {[file exists $proj_path]} {
    if {$force} {
        puts "INFO: -force given, removing existing project at $proj_path"
        file delete -force $proj_path
    } else {
        return -code error \
            "Project already exists at $proj_path. Re-run with -tclargs -force to replace it."
    }
}

#-----------------------------------------------------------------------------
# Board file. XHub installs board files per-user, under %APPDATA%/Xilinx on
# Windows or ~/.Xilinx on Linux -- NOT into the Vivado installation. A freshly
# created project does not scan that location by default, so board_part_repo_paths
# has to be pointed at it explicitly or set_property board_part silently
# resolves to nothing.
#-----------------------------------------------------------------------------
proc board_store_paths {} {
    set ver  [version -short]
    set tail [list xhub board_store xilinx_board_store]
    set candidates {}
    if {[info exists ::env(APPDATA)]} {
        lappend candidates [file join $::env(APPDATA) Xilinx Vivado $ver {*}$tail]
    }
    foreach var {HOME USERPROFILE} {
        if {[info exists ::env($var)]} {
            lappend candidates [file join $::env($var) .Xilinx Vivado $ver {*}$tail]
        }
    }
    set found {}
    foreach c $candidates {
        if {[file isdirectory $c]} { lappend found [file normalize $c] }
    }
    return [lsort -unique $found]
}

proc register_board_repos {} {
    set repos [board_store_paths]
    if {[llength $repos] == 0} { return 0 }
    set cur [get_property board_part_repo_paths [current_project]]
    set changed 0
    foreach r $repos {
        if {[lsearch -exact $cur $r] < 0} { lappend cur $r ; set changed 1 }
    }
    if {$changed} {
        set_property board_part_repo_paths $cur [current_project]
        puts "INFO: board repo paths: $cur"
    }
    return 1
}

proc ensure_board_part {board_part board_xhub} {
    register_board_repos
    if {[llength [get_board_parts -quiet $board_part]] > 0} {
        puts "INFO: board part '$board_part' available"
        return 1
    }
    puts "INFO: board part '$board_part' not found, attempting install from Xilinx Board Store"
    if {[catch {
        xhub::refresh_catalog [xhub::get_xstores xilinx_board_store]
        xhub::install [xhub::get_xitems $board_xhub]
    } err]} {
        puts "WARNING: board store install failed: $err"
    }
    # The install may have just created the directory, so re-register.
    register_board_repos
    if {[llength [get_board_parts -quiet $board_part]] > 0} {
        puts "INFO: board part '$board_part' installed"
        return 1
    }
    puts "WARNING: proceeding without a board part. Board-aware IP configuration"
    puts "WARNING: and the Board tab will be unavailable. Install it manually via"
    puts "WARNING: Tools > XHub Stores > Board Store > Digilent > Nexys A7-50T."
    return 0
}

#-----------------------------------------------------------------------------
# Create
#-----------------------------------------------------------------------------
file mkdir $proj_dir
create_project $proj_name $proj_path -part $part_name

set have_board [ensure_board_part $board_part $board_xhub]
if {$have_board} {
    set_property board_part $board_part [current_project]
    # set_property does not error when the board part cannot be resolved, it
    # just leaves the property empty. Check that it actually took.
    if {[get_property board_part [current_project]] ne $board_part} {
        return -code error "Failed to apply board part '$board_part' (property is empty after set_property)."
    }
    puts "INFO: board part applied: [get_property board_part [current_project]]"
}

set_property target_language        $target_language [current_project]
set_property simulator_language     $simulator_lang  [current_project]
set_property default_lib            xil_defaultlib   [current_project]

# Keep IP as an expanded directory tree rather than a single opaque container,
# so IP output stays diffable and cache-friendly.
set_property -name "ip_output_repo" -value [file join $proj_path "${proj_name}.cache" "ip"] \
    -objects [current_project]

#-----------------------------------------------------------------------------
# Sources. Everything is referenced IN PLACE from src/ and sim/ -- nothing is
# copied into the project tree, so git sees every edit.
#-----------------------------------------------------------------------------
proc collect {dir patterns} {
    set out {}
    if {![file isdirectory $dir]} { return $out }
    foreach pat $patterns {
        foreach f [glob -nocomplain -directory $dir -types f $pat] {
            lappend out [file normalize $f]
        }
    }
    foreach sub [glob -nocomplain -directory $dir -types d *] {
        foreach f [collect $sub $patterns] { lappend out $f }
    }
    return [lsort -unique $out]
}

set hdl_dir    [file join $repo_root "src" "hdl"]
set ip_dir     [file join $repo_root "src" "ip"]
set xdc_dir    [file join $repo_root "src" "constraints"]
set sim_dir    [file join $repo_root "sim"]

set hdl_files [collect $hdl_dir {*.v *.sv *.vh *.svh *.vhd *.vhdl}]
set ip_files  [collect $ip_dir  {*.xci *.bd}]
set xdc_files [collect $xdc_dir {*.xdc}]
set sim_files [collect $sim_dir {*.v *.sv *.vhd *.vhdl}]

if {[llength $hdl_files] > 0} {
    add_files -norecurse -fileset sources_1 $hdl_files
    puts "INFO: added [llength $hdl_files] design source(s)"
} else {
    puts "INFO: no design sources found under $hdl_dir"
}

if {[llength $ip_files] > 0} {
    add_files -norecurse -fileset sources_1 $ip_files
    puts "INFO: added [llength $ip_files] IP file(s)"
}

if {[llength $xdc_files] > 0} {
    add_files -norecurse -fileset constrs_1 $xdc_files
    puts "INFO: added [llength $xdc_files] constraint file(s)"
} else {
    puts "WARNING: no .xdc found under $xdc_dir -- the design will not pin out."
}

if {[llength $sim_files] > 0} {
    add_files -norecurse -fileset sim_1 $sim_files
    puts "INFO: added [llength $sim_files] simulation source(s)"
}

# Mark header files so Vivado treats them as includes, not compilation units.
foreach f [concat $hdl_files $sim_files] {
    if {[lsearch -exact {.vh .svh} [file extension $f]] >= 0} {
        set_property file_type "Verilog Header" [get_files $f]
    }
}

update_compile_order -fileset sources_1
if {[llength $sim_files] > 0} {
    update_compile_order -fileset sim_1
}

if {$explicit_top ne ""} {
    set_property top $explicit_top [get_filesets sources_1]
    puts "INFO: top set explicitly to '$explicit_top'"
}

#-----------------------------------------------------------------------------
# XSim defaults
#-----------------------------------------------------------------------------
set_property -name {xsim.simulate.runtime} -value {all} -objects [get_filesets sim_1]
set_property -name {xsim.simulate.log_all_signals} -value {true} -objects [get_filesets sim_1]

puts ""
puts "INFO: project created at $proj_path/$proj_name.xpr"
puts "INFO: top is '[get_property top [get_filesets sources_1]]'"
puts ""
