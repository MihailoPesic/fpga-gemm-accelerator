# Reconstruct the EXISTING native-DDR UART baseline. This is not a GEMM top.
# Refer to real tracked sources; keep imported/generated IP under build/.
set repo_root [file normalize [file join [file dirname [info script]] ..]]
set baseline_dir [file join $repo_root build native_ddr]
if {[llength $argv] != 0} { error "No options supported; output is build/native_ddr" }
if {[file exists [file join $baseline_dir native_ddr.xpr]]} {
    error "Project exists: use scripts/build.tcl, or choose a fresh checkout for reconstruction"
}
create_project native_ddr $baseline_dir -part xc7a50ticsg324-1L
source [file join $repo_root platform nexys_a7 board.tcl]
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
set old_sources [file join $repo_root {accelerator nexys.srcs}]
foreach name {top.sv uart_rx.sv uart_tx.sv uart_ddr_bridge.sv} {
    add_files -norecurse [list [file join $old_sources sources_1 new $name]]
}
foreach name {clk_wiz_0 mig_7series_0} {
    import_ip -files [list [file join $old_sources sources_1 ip $name ${name}.xci]]
}
add_files -fileset constrs_1 -norecurse [list [file join $old_sources constrs_1 new top.xdc]]
add_files -fileset sim_1 -norecurse [list [file join $old_sources sim_1 new tb_uart.sv]]
set_property top top [get_filesets sources_1]
set_property top tb_uart [get_filesets sim_1]
set_property xsim.simulate.runtime all [get_filesets sim_1]
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1
report_ip_status -file [file join $baseline_dir ip_status.txt]
puts "NATIVE_BASELINE_CREATED [file join $baseline_dir native_ddr.xpr]"
