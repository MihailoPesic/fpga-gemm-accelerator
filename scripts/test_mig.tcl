if {[llength $argv] != 1} { error "Use python scripts/test_mig.py" }
source [lindex $argv 0]
set_param general.maxThreads 1
set project_file [file join $mig_out project axi_mig.xpr]
if {[file exists $project_file]} {
    open_project $project_file
} else {
    create_project axi_mig [file join $mig_out project] -part xc7a50ticsg324-1L
    set_property target_language Verilog [current_project]
    set_property simulator_language Mixed [current_project]
    create_ip -name mig_7series -vendor xilinx.com -library ip -version 4.2 -module_name gemm_mig_axi
}
if {[get_property IS_LOCKED [get_ips gemm_mig_axi]]} { error "MIG is locked; review the installed IP version" }
# Reapply the checked configuration when reopening a project, including one
# previously inspected in the GUI. Never inherit a different memory preset.
set_property CONFIG.XML_INPUT_FILE [file join $mig_out mig_axi.prj] [get_ips gemm_mig_axi]
generate_target all [get_ips gemm_mig_axi]
generate_target example [get_ips gemm_mig_axi]
report_ip_status -file [file join $mig_out ip_status.txt]

# Add the generated vendor example to this project directly. Vivado 2026.1's
# open_example_project script passes one PRJ path as an unquoted file list,
# which fails when a repository path contains spaces. Keep generated HDL
# unchanged, and use proper Tcl lists for every source-file argument here.
set gen [file join $mig_out project axi_mig.gen sources_1 ip gemm_mig_axi gemm_mig_axi example_design]
set example_rtl [concat [glob [file join $gen rtl *.v]] [glob [file join $gen rtl traffic_gen *.v]]]
set example_sim [glob [file join $gen sim *.v*]]
# Keep the example outside the IP's composite file scope, as the vendor's
# example-project flow does. Copy bytes unchanged on every run.
set imports [file join $mig_out project imports]
file mkdir $imports
foreach fileset {sources_1 sim_1} files [list $example_rtl $example_sim] {
    foreach src $files {
        set dst [file join $imports [file tail $src]]
        file copy -force $src $dst
        if {![llength [get_files -quiet [list $dst]]]} {
            add_files -fileset $fileset -norecurse [list $dst]
        }
    }
}
set monitor [file join $mig_repo tb vendor tb_mig_axi.sv]
if {![llength [get_files -quiet [list $monitor]]]} {
    add_files -fileset sim_1 -norecurse [list $monitor]
}
set_property top example_top [get_filesets sources_1]
set_property top tb_mig_axi [get_filesets sim_1]
set_property xsim.simulate.runtime 0ns [get_filesets sim_1]
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1
launch_simulation
run all
close_sim
set fp [open [file join $mig_out simulation_pass.txt] w]
puts $fp [version]
close $fp
close_project
