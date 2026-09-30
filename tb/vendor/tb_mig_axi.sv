`timescale 1ns/1ps
// Observe AMD's generated AXI traffic generator and DDR2 memory-model test.
// The vendor test performs its own data comparison and finishes 50 us after
// FAST calibration. This wrapper also requires real successful AXI traffic.
module tb_mig_axi;
    sim_tb_top #(.C_S_AXI_ADDR_WIDTH(27)) vendor();
    integer read_bursts = 0, write_bursts = 0;
    integer read_beats = 0, write_beats = 0, write_responses = 0;
    realtime previous_edge = 0;
    realtime calibration_ns = 0;
    bit calibrated_seen = 0;
    logic [31:0] ar_min = 32'hffffffff, ar_max = 0;
    logic [31:0] aw_min = 32'hffffffff, aw_max = 0;

    always @(posedge vendor.u_ip_top.clk) begin
        if (calibrated_seen && (vendor.init_calib_complete !== 1'b1 ||
                                vendor.u_ip_top.aresetn !== 1'b1))
            $fatal(1, "MIG calibration/reset state lost after traffic began");
        if (vendor.init_calib_complete && vendor.u_ip_top.aresetn) begin
            calibrated_seen = 1;
            if (previous_edge != 0 &&
                (($realtime - previous_edge < 19.999) ||
                 ($realtime - previous_edge > 20.001)))
                $fatal(1, "MIG user clock is not 50 MHz");
            previous_edge = $realtime;
            if (vendor.tg_compare_error !== 1'b0)
                $fatal(1, "MIG traffic-generator data comparison failed");
            if (vendor.u_ip_top.s_axi_arvalid && vendor.u_ip_top.s_axi_arready) begin
                read_bursts = read_bursts + 1;
                if (vendor.u_ip_top.s_axi_araddr < ar_min) ar_min = vendor.u_ip_top.s_axi_araddr;
                if (vendor.u_ip_top.s_axi_araddr > ar_max) ar_max = vendor.u_ip_top.s_axi_araddr;
            end
            if (vendor.u_ip_top.s_axi_awvalid && vendor.u_ip_top.s_axi_awready) begin
                write_bursts = write_bursts + 1;
                if (vendor.u_ip_top.s_axi_awaddr < aw_min) aw_min = vendor.u_ip_top.s_axi_awaddr;
                if (vendor.u_ip_top.s_axi_awaddr > aw_max) aw_max = vendor.u_ip_top.s_axi_awaddr;
            end
            if (vendor.u_ip_top.s_axi_wvalid && vendor.u_ip_top.s_axi_wready)
                write_beats = write_beats + 1;
            if (vendor.u_ip_top.s_axi_rvalid && vendor.u_ip_top.s_axi_rready) begin
                if (vendor.u_ip_top.s_axi_rresp !== 2'b00)
                    $fatal(1, "MIG RRESP is not OKAY");
                read_beats = read_beats + 1;
            end
            if (vendor.u_ip_top.s_axi_bvalid && vendor.u_ip_top.s_axi_bready) begin
                if (vendor.u_ip_top.s_axi_bresp !== 2'b00)
                    $fatal(1, "MIG BRESP is not OKAY");
                write_responses = write_responses + 1;
            end
        end
    end

    initial begin
        wait (vendor.init_calib_complete === 1'b1);
        calibration_ns = $realtime;
    end

    initial begin
        #1000000;
        $fatal(1, "MIG example exceeded 1 ms simulation watchdog");
    end

    final begin
        if (!calibrated_seen || vendor.init_calib_complete !== 1'b1 ||
            vendor.u_ip_top.aresetn !== 1'b1 || vendor.tg_compare_error !== 1'b0 ||
            $realtime - previous_edge > 20.001 ||
            $realtime - calibration_ns < 49999.999 ||
            read_bursts == 0 || write_bursts == 0 || read_beats == 0 ||
            write_beats == 0 || write_responses == 0)
            $display("MIG_AXI_MONITOR_FAIL: clock, calibration or comparison state at finish");
        else begin
            $display("MIG_AXI_MONITOR_PASS calibration_ns=%0.3f read_bursts=%0d write_bursts=%0d read_beats=%0d write_beats=%0d write_responses=%0d", calibration_ns, read_bursts, write_bursts, read_beats, write_beats, write_responses);
            $display("MIG_AXI_ADDRESSES ar_min=%0d ar_max=%0d aw_min=%0d aw_max=%0d", ar_min, ar_max, aw_min, aw_max);
            $display("MIG_AXI_FINAL_PASS");
        end
    end
endmodule
