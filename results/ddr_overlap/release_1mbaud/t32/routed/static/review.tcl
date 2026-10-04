# Checkpoint-only review. Never modifies constraints, runs, placement or routing.
# argv: routed DCP, fresh output directory, caller-supplied scope label.
set_param general.maxThreads 1
if {[llength $argv] != 3} { error "Expected checkpoint output_directory scope" }
set review_dcp [file normalize [lindex $argv 0]]
set review_out [file normalize [lindex $argv 1]]
set review_scope [lindex $argv 2]
open_checkpoint $review_dcp

proc names {objects} {
    if {![llength $objects]} { return {} }
    return [lsort -unique [get_property NAME $objects]]
}
proc prop {key object} {
    global review_properties
    set group "[get_property CLASS $object]/[get_property REF_NAME $object]"
    if {![info exists review_properties($group)]} { set review_properties($group) [list_property $object] }
    if {[lsearch -exact $review_properties($group) $key] < 0} { return "" }
    return [get_property $key $object]
}
proc clock_names {cell} {
    global review_clocks
    set result {}
    foreach cp [get_pins -of_objects $cell -filter {IS_CLOCK}] {
        set clock_net [get_nets -quiet -of_objects $cp]
        if {![llength $clock_net]} {
            lappend result "UNCONNECTED_CLOCK_PIN:[get_property REF_PIN_NAME $cp]"
            continue
        }
        set key [names $clock_net]
        if {![info exists review_clocks($key)]} {
            set review_clocks($key) [names [get_clocks -quiet -of_objects $cp]]
        }
        foreach clk $review_clocks($key) { lappend result $clk }
    }
    return [lsort -unique $result]
}

set meta [open [file join $review_out review_scope.txt] w]
puts $meta "SCOPE $review_scope"
puts $meta "TOOL [version -short]"
puts $meta "DESIGN [current_design]"
puts $meta "CHECKPOINT $review_dcp"
puts $meta "No constraint/design/run changes; no bitstream or hardware access."
close $meta

# Preserve the qualified serial design's exact board-reset selector check.
set fp [open [file join $review_out reset_selector.txt] w]
puts $fp "Tool: [version -short]"
set cpu [names [all_fanout -flat -endpoints_only -from [get_ports CPU_RESETN]]]
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
        lappend selected [get_property NAME $pin]
        puts $fp "SELECTOR_ENDPOINT [get_property NAME $pin]"
    }
}
set same [expr {$cpu eq [lsort -unique $selected]}]
puts $fp "MIG_PRE=$pre MIG_PLL_RST=$pll UNEXPECTED=$unexpected"
puts $fp "Selector endpoint set equals CPU_RESETN endpoint set: $same"
close $fp
if {[llength $nets] != 1 || $pre != 43 || $pll != 1 || $unexpected || !$same} {
    error "Board reset differs from qualified serial bounded scope"
}

# Enumerate every reset-like primitive pin, including tied controls. Classification
# distinguishes actual async slice FF pins from synchronous FF reset inputs.
# Specialized PHY/clock/FIFO reset controls remain explicitly vendor review scope;
# a generic R/RST name is not evidence that the control is asynchronous.
set pins [get_pins -hier -filter {IS_LEAF && DIRECTION == IN && (IS_CLEAR || IS_PRESET || IS_SET || IS_RESET || IS_SETRESET || REF_PIN_NAME =~ *RST* || REF_PIN_NAME =~ *RESET*)}]
set inv [open [file join $review_out reset_inventory.tsv] w]
fconfigure $inv -buffering line
puts $inv "pin\tref_name\tref_pin\tclassification\tlogic_value\tasync_reg\tclock\tnet\tdirect_driver\tsr_type\tsetup_slack\thold_slack"
set async_pins {}
set specialist_pins {}
array set totals {}
foreach pin [lsort $pins] {
    set cell [get_cells -of_objects $pin]
    set ref [get_property REF_NAME $cell]
    set rp [get_property REF_PIN_NAME $pin]
    set kind VENDOR_PRIMITIVE_RESET_CONTROL
    if {$ref in {FDCE FDPE FDCP LDCE LDPE}} {
        set kind ASYNC_SLICE_RESET
        lappend async_pins $pin
    } elseif {$ref in {FDRE FDSE FDRS}} {
        set kind SYNC_SLICE_RESET
    } elseif {[string match RAMB* $ref] || $ref eq "DSP48E1"} {
        set kind SYNC_MEMORY_OR_DSP_RESET
    } elseif {$ref in {IDDR ODDR} && [prop SRTYPE $cell] eq "ASYNC"} {
        set kind ASYNC_DDR_IO_RESET
        lappend async_pins $pin
    } elseif {$ref in {IDDR ODDR} && [prop SRTYPE $cell] eq "SYNC"} {
        set kind SYNC_DDR_IO_RESET
    } else {
        lappend specialist_pins $pin
    }
    incr totals($kind)
    set net [get_nets -quiet -of_objects $pin]
    set drivers {}
    if {[llength $net]} {
        set key [names $net]
        if {![info exists review_drivers($key)]} {
            set review_drivers($key) [names [get_pins -quiet -leaf -of_objects $net -filter {DIRECTION == OUT}]]
            foreach port [get_ports -quiet -of_objects $net -filter {DIRECTION == IN}] { lappend review_drivers($key) [get_property NAME $port] }
        }
        set drivers $review_drivers($key)
    }
    set setup_slack ""
    set hold_slack ""
    if {$kind in {ASYNC_SLICE_RESET ASYNC_DDR_IO_RESET}} {
        set setup_slack [prop SETUP_SLACK $pin]
        set hold_slack [prop HOLD_SLACK $pin]
    }
    puts $inv [join [list [get_property NAME $pin] $ref $rp $kind [prop LOGIC_VALUE $pin] [prop ASYNC_REG $cell] [clock_names $cell] [names $net] [lsort -unique $drivers] [prop SRTYPE $cell] $setup_slack $hold_slack] "\t"]
}
close $inv
set reset_sum [open [file join $review_out reset_counts.txt] w]
foreach kind [lsort [array names totals]] { puts $reset_sum "$kind $totals($kind)" }
puts $reset_sum "ALL_RESET_LIKE_PRIMITIVE_PINS [llength $pins]"
puts $reset_sum "ASYNC_RESET_PINS [llength $async_pins]"
close $reset_sum

# Each asynchronous reset pin's fan-in and clock is retained, not only the board
# reset's direct leaves. Trace all arcs for an inventory independent of false paths.
set detail [open [file join $review_out async_reset_details.tsv] w]
puts $detail "pin\tref_name\tasync_reg\tclock\tstartpoints_all_arcs\tsetup_slack\thold_slack"
set async_outside_vendor 0
foreach pin [lsort $async_pins] {
    set cell [get_cells -of_objects $pin]
    set nm [get_property NAME $pin]
    set known_vendor 0
    foreach block {mig bridge reset_core reset_ui} {
        if {[string match "platform/platform/axi_ddr_bd_i/$block/*" $nm]} { set known_vendor 1 }
    }
    if {!$known_vendor} { incr async_outside_vendor }
    puts $detail [join [list $nm [get_property REF_NAME $cell] [prop ASYNC_REG $cell] [clock_names $cell] [names [all_fanin -flat -startpoints_only -trace_arcs all $pin]] [prop SETUP_SLACK $pin] [prop HOLD_SLACK $pin]] "\t"]
}
close $detail
if {$async_outside_vendor} { error "Async reset storage outside reviewed platform vendor scope" }

# No overrides or new exceptions are installed. Full exception reports are needed
# because report_exceptions -to does not match a through-net false-path constraint.
report_exceptions -file [file join $review_out exceptions.txt]
report_exceptions -coverage -file [file join $review_out exceptions_coverage.txt]
report_exceptions -ignored -file [file join $review_out exceptions_ignored.txt]
report_exceptions -scope_override -file [file join $review_out exceptions_scope_override.txt]
report_exceptions -write_valid_exceptions -file [file join $review_out valid_exceptions.xdc]
report_exceptions -write_merged_exceptions -file [file join $review_out merged_exceptions.xdc]
report_timing -to $async_pins -delay_type min_max -max_paths 200 -nworst 1 -path_type full_clock_expanded -file [file join $review_out async_reset_timing.txt]
report_timing -to $async_pins -delay_type min_max -user_ignored -max_paths 200 -nworst 1 -path_type full_clock_expanded -file [file join $review_out async_reset_ignored_timing.txt]
if {[llength $specialist_pins]} {
    report_timing -to $specialist_pins -delay_type min_max -max_paths 400 -nworst 1 -path_type full_clock_expanded -file [file join $review_out vendor_reset_control_timing.txt]
    report_timing -to $specialist_pins -delay_type min_max -user_ignored -max_paths 400 -nworst 1 -path_type full_clock_expanded -file [file join $review_out vendor_reset_control_ignored_timing.txt]
}

foreach {ref pattern file} {DSP48E1 DSP48E1 dsp_cells.tsv BRAM RAMB* bram_cells.tsv} {
    set resource_out [open [file join $review_out $file] w]
    puts $resource_out "cell\tref_name\torig_cell_name\torig_ref_name\tloc"
    foreach cell [lsort [get_cells -hier -filter "REF_NAME =~ $pattern"]] {
        puts $resource_out [join [list [get_property NAME $cell] [get_property REF_NAME $cell] [prop ORIG_CELL_NAME $cell] [prop ORIG_REF_NAME $cell] [prop LOC $cell]] "\t"]
    }
    close $resource_out
}

# Preserve clock/CDC/check-timing review independently of the builder's reports.
report_clocks -file [file join $review_out clocks.txt]
report_clock_interaction -file [file join $review_out clock_interaction.txt]
report_utilization -hierarchical -file [file join $review_out utilization_hierarchical.txt]
report_cdc -name reset_review_cdc -details -show_waiver -file [file join $review_out cdc.txt]
check_timing -verbose -file [file join $review_out check_timing.txt]
puts "CHECKPOINT_RESET_CLOCK_REVIEW_COMPLETE ASYNC=[llength $async_pins] RESET_LIKE=[llength $pins]"
close_design
