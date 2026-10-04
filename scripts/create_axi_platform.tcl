# Source after setting ddr_root/ddr_out, or pass one config.tcl as -tclargs.
# The caller derives ddr_out/mig_axi.prj with test_mig.py:derive_configuration.
if {![info exists ddr_root] || ![info exists ddr_out]} {
    if {[llength $argv] != 1} { error "Set ddr_root and ddr_out or pass a configuration Tcl file" }
    source [lindex $argv 0]
}
set ddr_root [file normalize $ddr_root]
set ddr_out [file normalize $ddr_out]
set ddr_prj [file join $ddr_out mig_axi.prj]
if {![file exists $ddr_prj]} { error "Missing derived AXI MIG configuration: $ddr_prj" }
set_param general.maxThreads 1
set project_file [file join $ddr_out project axi_platform.xpr]
if {[file exists $project_file]} {
    error "Use a fresh ddr_out; refusing to replace an existing AXI platform project"
}
create_project axi_platform [file join $ddr_out project] -part xc7a50ticsg324-1L
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
set_property XPM_LIBRARIES {XPM_CDC} [current_project]
create_bd_design axi_ddr_bd

create_bd_port -dir I -type clk -freq_hz 100000000 CLK100MHZ
create_bd_port -dir I -type rst CPU_RESETN
set_property CONFIG.POLARITY ACTIVE_LOW [get_bd_ports CPU_RESETN]
create_bd_port -dir O -type clk core_clk
create_bd_port -dir O -type rst core_rst
set_property CONFIG.POLARITY ACTIVE_HIGH [get_bd_ports core_rst]
create_bd_port -dir O calibrated_ui
create_bd_intf_port -mode Slave -vlnv xilinx.com:interface:aximm_rtl:1.0 S_AXI
set_property -dict {CONFIG.ASSOCIATED_BUSIF S_AXI CONFIG.ASSOCIATED_RESET core_rst} [get_bd_ports core_clk]
set_property -dict [list CONFIG.PROTOCOL AXI4 CONFIG.ADDR_WIDTH 32 CONFIG.DATA_WIDTH 64 \
    CONFIG.ID_WIDTH 1 CONFIG.HAS_BURST 1 CONFIG.HAS_LOCK 1 CONFIG.HAS_CACHE 1 \
    CONFIG.HAS_PROT 1 CONFIG.HAS_QOS 1 CONFIG.HAS_REGION 0 CONFIG.HAS_WSTRB 1 \
    CONFIG.HAS_RRESP 1 CONFIG.HAS_BRESP 1 CONFIG.SUPPORTS_NARROW_BURST 0 \
    CONFIG.MAX_BURST_LENGTH 16 CONFIG.NUM_READ_OUTSTANDING 4 \
    CONFIG.NUM_WRITE_OUTSTANDING 1 CONFIG.NUM_READ_THREADS 1 CONFIG.NUM_WRITE_THREADS 1 \
    CONFIG.READ_WRITE_MODE READ_WRITE] [get_bd_intf_ports S_AXI]

create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clocks
set_property -dict [list CONFIG.PRIM_IN_FREQ 100.000 CONFIG.CLKIN1_JITTER_PS 100.0 \
    CONFIG.PRIM_SOURCE Single_ended_clock_capable_pin CONFIG.USE_RESET false \
    CONFIG.USE_LOCKED true CONFIG.CLKOUT1_REQUESTED_OUT_FREQ 100.000 \
    CONFIG.CLKOUT2_USED true CONFIG.CLKOUT2_REQUESTED_OUT_FREQ 200.000] [get_bd_cells clocks]
create_bd_cell -type ip -vlnv xilinx.com:ip:mig_7series:4.2 mig
set_property CONFIG.XML_INPUT_FILE $ddr_prj [get_bd_cells mig]
# SmartConnect packs multibeat bursts to 128 bits. A shorter single transfer
# retains its SIZE, but is not a narrow burst (PG247). MIG handles that transfer
# with aligned native-word access and byte strobes; its extra burst upsizer is
# unnecessary. The caller supplies narrow-burst support=0 in this platform XML.
create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 bridge
set_property -dict [list CONFIG.NUM_SI 1 CONFIG.NUM_MI 1 CONFIG.NUM_CLKS 2 \
    CONFIG.HAS_ARESETN 1 CONFIG.ADVANCED_PROPERTIES \
    {__view__ {functional {S00_Entry {SUPPORTS_WRAP 0 SUPPORTS_DECERR 1}}}}] [get_bd_cells bridge]

create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 zero
set_property -dict {CONFIG.CONST_WIDTH 1 CONFIG.CONST_VAL 0} [get_bd_cells zero]
create_bd_cell -type ip -vlnv xilinx.com:ip:util_vector_logic:2.0 mig_reset_gate
set_property -dict {CONFIG.C_SIZE 1 CONFIG.C_OPERATION and} [get_bd_cells mig_reset_gate]
foreach name {reset_core reset_ui} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 $name
    # C_EXT_RESET_HIGH is propagated from MIG's active-high reset metadata.
    set_property -dict [list CONFIG.C_AUX_RESET_HIGH 1 \
        CONFIG.C_EXT_RST_WIDTH 4 CONFIG.C_AUX_RST_WIDTH 4 \
        CONFIG.C_NUM_BUS_RST 1 CONFIG.C_NUM_PERP_RST 1 \
        CONFIG.C_NUM_INTERCONNECT_ARESETN 1 CONFIG.C_NUM_PERP_ARESETN 1] [get_bd_cells $name]
    connect_bd_net [get_bd_pins zero/dout] [get_bd_pins $name/aux_reset_in] [get_bd_pins $name/mb_debug_sys_rst]
    connect_bd_net [get_bd_pins clocks/locked] [get_bd_pins $name/dcm_locked]
    connect_bd_net [get_bd_pins mig/ui_clk_sync_rst] [get_bd_pins $name/ext_reset_in]
}

connect_bd_net [get_bd_ports CLK100MHZ] [get_bd_pins clocks/clk_in1]
connect_bd_net [get_bd_ports CPU_RESETN] [get_bd_pins mig_reset_gate/Op1]
connect_bd_net [get_bd_pins clocks/locked] [get_bd_pins mig_reset_gate/Op2]
connect_bd_net [get_bd_pins mig_reset_gate/Res] [get_bd_pins mig/sys_rst]
connect_bd_net [get_bd_pins clocks/clk_out1] [get_bd_ports core_clk] \
    [get_bd_pins mig/sys_clk_i] [get_bd_pins bridge/aclk] [get_bd_pins reset_core/slowest_sync_clk]
connect_bd_net [get_bd_pins clocks/clk_out2] [get_bd_pins mig/clk_ref_i]
connect_bd_net [get_bd_pins mig/ui_clk] [get_bd_pins bridge/aclk1] [get_bd_pins reset_ui/slowest_sync_clk]
connect_bd_net [get_bd_pins reset_core/peripheral_reset] [get_bd_ports core_rst]
connect_bd_net [get_bd_pins reset_ui/interconnect_aresetn] [get_bd_pins bridge/aresetn]
connect_bd_net [get_bd_pins reset_ui/peripheral_aresetn] [get_bd_pins mig/aresetn]
connect_bd_net [get_bd_pins mig/init_calib_complete] [get_bd_ports calibrated_ui]
connect_bd_intf_net [get_bd_intf_ports S_AXI] [get_bd_intf_pins bridge/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins bridge/M00_AXI] [get_bd_intf_pins mig/S_AXI]
make_bd_intf_pins_external [get_bd_intf_pins mig/DDR2]
set ddr_external [get_bd_intf_ports -filter {NAME != S_AXI}]
if {[llength $ddr_external] != 1} { error "Expected one external DDR interface" }
set_property name DDR2 $ddr_external

# Decode the full 32-bit source address before narrowing to MIG's 27-bit port.
# Invalid source addresses are DECERR, never a truncated access to DDR address0.
set memory_segments [get_bd_addr_segs -of_objects [get_bd_intf_pins mig/S_AXI]]
if {[llength $memory_segments] != 1} { error "Expected one MIG address segment" }
assign_bd_address -offset 0x00000000 -range 0x08000000 \
    -target_address_space [get_bd_addr_spaces S_AXI] $memory_segments
validate_bd_design
foreach {interface property expected} {
    S_AXI CONFIG.ADDR_WIDTH 32 S_AXI CONFIG.DATA_WIDTH 64
    S_AXI CONFIG.FREQ_HZ 100000000
    mig/S_AXI CONFIG.ADDR_WIDTH 27 mig/S_AXI CONFIG.DATA_WIDTH 128
    mig/S_AXI CONFIG.FREQ_HZ 50000000 mig/S_AXI CONFIG.SUPPORTS_NARROW_BURST 0
} {
    set object [get_bd_intf_pins -quiet $interface]
    if {![llength $object]} { set object [get_bd_intf_ports $interface] }
    if {[get_property $property $object] != $expected} {
        error "Unexpected $interface $property: expected $expected"
    }
}
save_bd_design
set bd_file [get_files [list [file join $ddr_out project axi_platform.srcs sources_1 bd axi_ddr_bd axi_ddr_bd.bd]]]
set_property synth_checkpoint_mode None $bd_file
generate_target all $bd_file
set generated_wrapper [make_wrapper -files $bd_file -top]
add_files -norecurse $generated_wrapper
add_files -norecurse [list [file join $ddr_root platform nexys_a7 axi_ddr_platform.sv]]
add_files -fileset constrs_1 -norecurse [list [file join $ddr_root platform nexys_a7 axi_ddr.xdc]]
set_property top axi_ddr_platform [get_filesets sources_1]
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1
report_ip_status -file [file join $ddr_out ip_status.txt]
write_bd_tcl -force [file join $ddr_out generated_platform.tcl]
set report [open [file join $ddr_out platform_configuration.txt] w]
puts $report [version]
puts $report "PART=[get_property PART [current_project]]"
foreach object [concat [get_bd_intf_ports S_AXI] [get_bd_intf_pins bridge/S00_AXI] \
    [get_bd_intf_pins bridge/M00_AXI] [get_bd_intf_pins mig/S_AXI] \
    [get_bd_ports core_clk] [get_bd_addr_segs -of_objects [get_bd_addr_spaces S_AXI]]] {
    puts $report "OBJECT=$object"
    foreach property [lsort [list_property $object]] {
        if {[string match CONFIG.* $property] || $property in {OFFSET RANGE}} {
            puts $report "$property=[get_property $property $object]"
        }
    }
}
close $report
puts "AXI_DDR_PLATFORM_GENERATED $project_file"
# Leave the project open so the caller can add simulation or a board top.
