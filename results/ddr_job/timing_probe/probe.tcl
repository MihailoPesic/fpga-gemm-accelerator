set out [file normalize [file dirname [info script]]]
cd $out
set_param general.maxThreads 1
create_project -in_memory -part xc7a50ticsg324-1L
read_verilog -sv [list gemm_ddr_job.sv gemm_ddr_job_probe.sv]
synth_design -top gemm_ddr_job_probe -mode out_of_context -part xc7a50ticsg324-1L
create_clock -name core -period 10 [get_ports clk]
set_clock_uncertainty 0.200 [get_clocks core]
set_false_path -from [get_ports -filter {DIRECTION == IN && NAME != clk}]
set_false_path -to [all_outputs]
set_property LOC BUFGCTRL_X0Y0 [get_cells clock_buffer]
opt_design
place_design
route_design
report_utilization -file utilization.txt
report_timing_summary -delay_type min_max -report_unconstrained -max_paths 5 -file timing.txt
report_methodology -file methodology.txt
check_timing -verbose -file check_timing.txt
report_route_status -file route.txt
write_checkpoint -force routed.dcp
set wns [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set whs [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
set dsp [llength [get_cells -quiet -hier -filter {REF_NAME == DSP48E1}]]
puts "DDR_JOB_PROBE_RESULT WNS=$wns WHS=$whs DSP=$dsp"
