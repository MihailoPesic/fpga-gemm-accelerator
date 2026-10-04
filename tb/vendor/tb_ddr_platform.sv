`timescale 1ns/1ps
// The complete board datapath against the unmodified generated x16 DDR2 model.
// Command transport is tested separately; this fixture starts the same BIST
// controller at its internal pulse interface to avoid simulating UART delays.
module tb_ddr_platform #(parameter WARM_RESET = 0);
    logic CLK100MHZ = 0;
    always #5 CLK100MHZ = !CLK100MHZ;
    logic CPU_RESETN = 0;
    wire UART_RXD_OUT;
    wire [3:0] LED;
    wire [12:0] ddr2_addr;
    wire [2:0] ddr2_ba;
    wire ddr2_ras_n, ddr2_cas_n, ddr2_we_n;
    wire [0:0] ddr2_ck_p, ddr2_ck_n, ddr2_cke, ddr2_cs_n, ddr2_odt;
    wire [1:0] ddr2_dm;
    wire [15:0] ddr2_dq;
    wire [1:0] ddr2_dqs_p, ddr2_dqs_n;
    gemm_ddr_diag_top dut (.UART_TXD_IN(1'b1), .*);
    ddr2_model #(.DEBUG(0)) memory (
        .ck(ddr2_ck_p[0]), .ck_n(ddr2_ck_n[0]), .cke(ddr2_cke[0]),
        .cs_n(ddr2_cs_n[0]), .ras_n(ddr2_ras_n), .cas_n(ddr2_cas_n),
        .we_n(ddr2_we_n), .dm_rdqs(ddr2_dm), .ba(ddr2_ba), .addr(ddr2_addr),
        .dq(ddr2_dq), .dqs(ddr2_dqs_p), .dqs_n(ddr2_dqs_n), .rdqs_n(), .odt(ddr2_odt[0])
    );
    integer successful_runs = 0, accepted_r = 0, accepted_w = 0, accepted_b = 0;
    logic allow_decode_error = 0;
    logic [31:0] requested_seed;
    realtime previous_core_edge = 0;

    always @(posedge dut.core_clk) begin
        if (!dut.core_rst) begin
            if (previous_core_edge != 0 &&
                ($realtime-previous_core_edge < 9.999 || $realtime-previous_core_edge > 10.001))
                $fatal(1, "Core clock is not 100 MHz");
            previous_core_edge = $realtime;
            if (dut.axi_rvalid && dut.axi_rready) begin
                accepted_r = accepted_r + 1;
                if (dut.axi_rresp !== 0 && !(allow_decode_error && dut.axi_rresp === 3))
                    $fatal(1, "Unexpected RRESP");
                if (dut.axi_rresp === 0 && $isunknown(dut.axi_rdata))
                    $fatal(1, "Successful memory response contains unknown data");
            end
            if (dut.axi_wvalid && dut.axi_wready) accepted_w = accepted_w + 1;
            if (dut.axi_bvalid && dut.axi_bready) begin
                accepted_b = accepted_b + 1;
                if (dut.axi_bresp !== 0) $fatal(1, "Unexpected BRESP");
            end
        end else previous_core_edge = 0;
    end

    task run_diagnostic(input logic [31:0] new_seed);
        begin
            wait (dut.ddr_ready === 1'b1 && dut.core_rst === 1'b0);
            @(negedge dut.core_clk);
            requested_seed = new_seed;
            force dut.seed = requested_seed;
            force dut.start = 1'b1;
            @(negedge dut.core_clk);
            release dut.start;
            wait (dut.busy === 1'b1);
            wait (dut.busy === 1'b0);
            #1;
            if (dut.done !== 1'b1 || dut.error !== 1'b0 || dut.engine_fatal !== 1'b0 ||
                dut.axi_quiescent !== 1'b1 || dut.ddr_ready !== 1'b1)
                $fatal(1, "Diagnostic failed code=%h addr=%h expected=%h actual=%h",
                       dut.error_code, dut.first_fail_addr, dut.expected, dut.actual);
            if (dut.read_beats !== 64'd1024 || dut.write_beats !== 64'd648)
                $fatal(1, "Unexpected diagnostic transfer coverage");
            successful_runs = successful_runs + 1;
            $display("DDR_DIAG_RUN_PASS seed=%h cycles=%0d read_beats=%0d write_beats=%0d",
                     new_seed, dut.cycles, dut.read_beats, dut.write_beats);
            release dut.seed;
        end
    endtask

    initial begin
        #2000 CPU_RESETN = 1;
        run_diagnostic(32'h12345678);
        run_diagnostic(32'ha5c319e7);
        run_diagnostic(32'h0f1e2d3c);
        if (accepted_r !== 3072 || accepted_w !== 1944 || accepted_b !== 144)
            $fatal(1, "AXI transfer/completion counts differ from successful local counts");

        // Read only the upper 64-bit lane of a MIG word. Slot0's first overlay
        // inverted its low four bytes; this constant is independently calculated
        // from seed 0f1e2d3c and byte address8 using the documented pattern.
        @(negedge dut.core_clk);
        force dut.rd_cmd_addr = 32'h00000008;
        force dut.rd_cmd_beats = 5'd1;
        force dut.rd_cmd_tag = 16'h1234;
        force dut.rd_cmd_valid = 1'b1;
        do @(posedge dut.core_clk); while (!dut.rd_cmd_ready);
        @(negedge dut.core_clk);
        release dut.rd_cmd_valid;
        release dut.rd_cmd_addr;
        release dut.rd_cmd_beats;
        release dut.rd_cmd_tag;
        wait (dut.rd_data_valid === 1'b1);
        if (dut.rd_data !== 64'h3370de46aaddb72b || dut.rd_data_last !== 1'b1)
            $fatal(1, "Single-beat upper-lane read failed: %h", dut.rd_data);
        wait (dut.rd_done_valid === 1'b1);
        if (dut.rd_done_status !== 16'd0 || dut.engine_fatal !== 1'b0)
            $fatal(1, "Single-beat upper-lane read completion failed");
        $display("DDR_SINGLE_READ_PASS address=00000008 data=3370de46aaddb72b");
        @(posedge dut.core_clk);

        // The first address above 128 MiB must produce DECERR, not read DDR0.
        @(negedge dut.core_clk);
        allow_decode_error = 1;
        force dut.rd_cmd_addr = 32'h08000000;
        force dut.rd_cmd_beats = 5'd1;
        force dut.rd_cmd_tag = 16'hdead;
        force dut.rd_cmd_valid = 1'b1;
        do @(posedge dut.core_clk); while (!dut.rd_cmd_ready);
        @(negedge dut.core_clk);
        release dut.rd_cmd_valid;
        release dut.rd_cmd_addr;
        release dut.rd_cmd_beats;
        release dut.rd_cmd_tag;
        wait (dut.rd_done_valid === 1'b1);
        if (dut.rd_done_status !== 16'd7 || dut.engine_fatal !== 1'b1)
            $fatal(1, "Out-of-range address did not become a fatal memory response");
        $display("DDR_DECODE_PASS address=08000000 status=7");

        if (successful_runs !== 3 || accepted_r !== 3074 || accepted_w !== 1944 || accepted_b !== 144)
            $fatal(1, "Missing repeat/transaction coverage");
        $display("DDR_PLATFORM_PASS runs=%0d read_beats=%0d write_beats=%0d write_responses=%0d",
                 successful_runs, accepted_r, accepted_w, accepted_b);
        if (WARM_RESET) begin
            // Separate qualification: never reset or bypass the DRAM model.
            // A functional recovery marker does not excuse its timing errors.
            @(negedge CLK100MHZ) CPU_RESETN = 0;
            #2000;
            allow_decode_error = 0;
            CPU_RESETN = 1;
            run_diagnostic(32'h13579bdf);
            if (successful_runs !== 4 || accepted_r !== 4098 || accepted_w !== 2592 || accepted_b !== 192)
                $fatal(1, "Missing warm-reset transaction coverage");
            $display("DDR_RESET_FUNCTIONAL_PASS");
        end
        $finish;
    end
    initial begin
        #2000000;
        $fatal(1, "DDR platform simulation exceeded 2 ms watchdog");
    end
endmodule
