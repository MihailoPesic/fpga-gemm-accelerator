# Resource inference check for operand storage plus compute; not routed timing.
set root [file normalize [file join [file dirname [info script]] ..]]
set out [file join $root build synth_memory]
file mkdir $out
cd $out
set_param general.maxThreads 4
set sources [concat [glob [file join $root rtl core *.sv]] [glob [file join $root rtl memory *.sv]]]
set records [list]
foreach geometry {{4 8} {4 32} {8 8} {8 32}} {
    lassign $geometry p t
    create_project -in_memory -part xc7a50ticsg324-1L
    read_verilog -sv $sources
    synth_design -top gemm_bram_microtile -mode out_of_context -part xc7a50ticsg324-1L -generic [list P=$p T=$t]
    set dsp [llength [get_cells -hier -filter {REF_NAME == DSP48E1}]]
    set ram36 [llength [get_cells -hier -filter {REF_NAME == RAMB36E1}]]
    set ram18 [llength [get_cells -hier -filter {REF_NAME == RAMB18E1}]]
    if {$dsp != $p*$p} { error "Expected [expr {$p*$p}] DSPs; found $dsp" }
    if {$ram36 + $ram18 == 0} { error "Operand arrays did not infer block RAM" }
    report_utilization -file p${p}_t${t}_utilization.txt
    report_utilization -hierarchical -file p${p}_t${t}_hierarchy.txt
    lappend records "P=$p T=$t DSP=$dsp RAMB36=$ram36 RAMB18=$ram18"
    puts "GEMM_MEMORY_SYNTH_PASS [lindex $records end]"
    close_project
}
set fp [open summary.txt w]
puts $fp "Vivado [version -short]; xc7a50ticsg324-1L; synthesis only, no timing claim"
puts $fp [join $records \n]
close $fp
