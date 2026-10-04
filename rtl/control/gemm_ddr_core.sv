`timescale 1ns/1ps
// DDR GEMM subsystem. UART byte framing terminates here; a board wrapper
// supplies the PHY, calibrated status and vendor AXI width/clock conversion.
module gemm_ddr_core #(
    parameter integer P=4, T=32, Q_W=$clog2(T), DIM_W=$clog2(T+1), READ_SLOTS=1,
    parameter integer ENABLE_OVERLAP=0,
    parameter logic [31:0] BUILD_ID=32'd0, CORE_HZ=32'd100000000,
    parameter logic [31:0] ID=32'h314d474e,
    parameter logic [31:0] VERSION=ENABLE_OVERLAP ? 32'h00000200 : 32'h00000100,
    parameter logic [32:0] DDR_BYTES=33'd134217728
) (
    input wire clk, rst, ddr_ready,
    input wire rx_valid, rx_error,
    input wire [7:0] rx_data,
    output wire tx_valid,
    input wire tx_ready,
    output wire [7:0] tx_data,
    output wire ready, busy, done, error, reset_required,
    output wire [15:0] error_code,
    output wire [31:0] last_job_id,
    output wire host_busy, job_accepted,
    output wire [0:0] m_axi_arid,
    output wire [31:0] m_axi_araddr,
    output wire [7:0] m_axi_arlen,
    output wire [2:0] m_axi_arsize,
    output wire [1:0] m_axi_arburst,
    output wire m_axi_arlock,
    output wire [3:0] m_axi_arcache,
    output wire [2:0] m_axi_arprot,
    output wire [3:0] m_axi_arqos, m_axi_arregion,
    output wire m_axi_arvalid,
    input wire m_axi_arready,
    input wire [0:0] m_axi_rid,
    input wire [63:0] m_axi_rdata,
    input wire [1:0] m_axi_rresp,
    input wire m_axi_rlast, m_axi_rvalid,
    output wire m_axi_rready,
    output wire [0:0] m_axi_awid,
    output wire [31:0] m_axi_awaddr,
    output wire [7:0] m_axi_awlen,
    output wire [2:0] m_axi_awsize,
    output wire [1:0] m_axi_awburst,
    output wire m_axi_awlock,
    output wire [3:0] m_axi_awcache,
    output wire [2:0] m_axi_awprot,
    output wire [3:0] m_axi_awqos, m_axi_awregion,
    output wire m_axi_awvalid,
    input wire m_axi_awready,
    output wire [63:0] m_axi_wdata,
    output wire [7:0] m_axi_wstrb,
    output wire m_axi_wlast, m_axi_wvalid,
    input wire m_axi_wready,
    input wire [0:0] m_axi_bid,
    input wire [1:0] m_axi_bresp,
    input wire m_axi_bvalid,
    output wire m_axi_bready
);
    wire req_valid, req_ready, rsp_valid, rsp_ready;
    wire [7:0] req_opcode;
    wire [8:0] req_length, rsp_length;
    wire [2047:0] req_payload;
    wire [1919:0] rsp_payload;
    wire [15:0] rsp_status;
    wire reg_req_valid, reg_req_ready, reg_req_write, reg_rsp_valid, reg_rsp_ready;
    wire [15:0] reg_req_addr, reg_rsp_status;
    wire [31:0] reg_req_wdata, reg_rsp_rdata;
    wire [3:0] reg_req_wstrb;
    wire reg_busy_guard, host_fatal;
    wire [15:0] host_fatal_code;
    wire [31:0] cfg_job_id, cfg_m, cfg_n, cfg_k, cfg_a_base, cfg_bt_base, cfg_c_base;
    wire [31:0] cfg_a_stride, cfg_bt_stride, cfg_c_stride, cfg_mode, cfg_watchdog;
    wire job_start_valid, job_start_ready, job_start_rsp_valid, job_start_rsp_ready;
    wire [15:0] job_start_rsp_status, job_clear_status_code;
    wire job_clear_status;
    wire [63:0] job_cycles, compute_cycles, read_beats, write_beats;
    wire [63:0] write_valid_bytes, input_wait_cycles, read_stall_cycles, write_stall_cycles;

    wire dma_req_valid, dma_req_ready, dma_req_write, dma_req_bt, dma_req_buf;
    wire [31:0] dma_req_base, dma_req_stride;
    wire [5:0] dma_req_rows;
    wire [8:0] dma_req_row_bytes;
    wire dma_busy, dma_done_valid, dma_done_ready, dma_fatal;
    wire [15:0] dma_done_status, dma_fatal_code;
    wire load_req_valid, load_req_ready, load_req_bt, load_req_buf;
    wire store_req_valid, store_req_ready, store_req_buf;
    wire [31:0] load_req_base, load_req_stride, store_req_base, store_req_stride;
    wire [5:0] load_req_rows, store_req_rows;
    wire [8:0] load_req_row_bytes, store_req_row_bytes;
    wire load_busy, load_done_valid, load_done_ready, store_busy, store_done_valid, store_done_ready;
    wire [15:0] load_done_status, store_done_status;
    wire compute_start, compute_ready, compute_busy, compute_done, compute_error;
    wire compute_input_buf, compute_output_buf;
    wire [DIM_W-1:0] compute_m, compute_n;
    wire [8:0] compute_k;
    wire [63:0] compute_cycles_in;
    wire load_valid, load_ready, load_bt, load_buf;
    wire [Q_W-1:0] load_q;
    wire [4:0] load_word;
    wire [63:0] load_data;
    wire read_valid, read_ready, read_buf, response_valid, response_ready, response_error;
    wire [Q_W-1:0] read_row;
    wire [Q_W-2:0] read_pair;
    wire [63:0] response_data;
    wire [7:0] response_strb;

    // h_* belongs to host commands, d_* to tile DMA, b_* to the burst engine.
    // Ownership is whole-operation and remains host-owned through fault drain.
    wire h_rd_cmd_valid, h_rd_cmd_ready, d_rd_cmd_valid, d_rd_cmd_ready;
    wire h_wr_cmd_valid, h_wr_cmd_ready, d_wr_cmd_valid, d_wr_cmd_ready;
    wire [31:0] h_rd_cmd_addr, d_rd_cmd_addr, h_wr_cmd_addr, d_wr_cmd_addr;
    wire [4:0] h_rd_cmd_beats, d_rd_cmd_beats, h_wr_cmd_beats, d_wr_cmd_beats;
    wire [15:0] h_rd_cmd_tag, d_rd_cmd_tag, h_wr_cmd_tag, d_wr_cmd_tag;
    wire h_rd_data_valid, h_rd_data_ready, d_rd_data_valid, d_rd_data_ready;
    wire h_rd_done_valid, h_rd_done_ready, d_rd_done_valid, d_rd_done_ready;
    wire h_wr_data_valid, h_wr_data_ready, d_wr_data_valid, d_wr_data_ready;
    wire [63:0] h_wr_data, d_wr_data;
    wire [7:0] h_wr_data_strb, d_wr_data_strb;
    wire h_wr_done_valid, h_wr_done_ready, d_wr_done_valid, d_wr_done_ready;
    wire b_rd_cmd_valid, b_rd_cmd_ready, b_wr_cmd_valid, b_wr_cmd_ready;
    wire [31:0] b_rd_cmd_addr, b_wr_cmd_addr;
    wire [4:0] b_rd_cmd_beats, b_wr_cmd_beats;
    wire [15:0] b_rd_cmd_tag, b_wr_cmd_tag;
    wire b_rd_data_valid, b_rd_data_ready, b_rd_data_last;
    wire [63:0] b_rd_data;
    wire [3:0] b_rd_data_index;
    wire [15:0] b_rd_data_tag;
    wire b_rd_done_valid, b_rd_done_ready, b_wr_done_valid, b_wr_done_ready;
    wire [15:0] b_rd_done_status, b_rd_done_tag, b_wr_done_status, b_wr_done_tag;
    wire b_wr_data_valid, b_wr_data_ready;
    wire [63:0] b_wr_data;
    wire [7:0] b_wr_data_strb;
    wire burst_fatal, axi_quiescent, burst_local_idle, mem_progress;
    wire [15:0] burst_fatal_code;
    // Only registered fault signals return to the adapters. The job's first
    // latched code remains authoritative if a later drain response also faults.
    wire mem_fatal = reset_required || host_fatal || burst_fatal;
    wire [15:0] mem_fatal_code = reset_required ? error_code :
        host_fatal ? host_fatal_code : burst_fatal_code;
    wire control_mem_fatal = reset_required || burst_fatal;
    wire [15:0] control_mem_fatal_code = reset_required ? error_code : burst_fatal_code;

    assign b_rd_cmd_valid = host_busy ? h_rd_cmd_valid : d_rd_cmd_valid;
    assign b_rd_cmd_addr = host_busy ? h_rd_cmd_addr : d_rd_cmd_addr;
    assign b_rd_cmd_beats = host_busy ? h_rd_cmd_beats : d_rd_cmd_beats;
    assign b_rd_cmd_tag = host_busy ? h_rd_cmd_tag : d_rd_cmd_tag;
    assign h_rd_cmd_ready = host_busy && b_rd_cmd_ready;
    assign d_rd_cmd_ready = !host_busy && b_rd_cmd_ready;
    assign h_rd_data_valid = host_busy && b_rd_data_valid;
    assign d_rd_data_valid = !host_busy && b_rd_data_valid;
    assign b_rd_data_ready = host_busy ? h_rd_data_ready : d_rd_data_ready;
    assign h_rd_done_valid = host_busy && b_rd_done_valid;
    assign d_rd_done_valid = !host_busy && b_rd_done_valid;
    assign b_rd_done_ready = host_busy ? h_rd_done_ready : d_rd_done_ready;
    assign b_wr_cmd_valid = host_busy ? h_wr_cmd_valid : d_wr_cmd_valid;
    assign b_wr_cmd_addr = host_busy ? h_wr_cmd_addr : d_wr_cmd_addr;
    assign b_wr_cmd_beats = host_busy ? h_wr_cmd_beats : d_wr_cmd_beats;
    assign b_wr_cmd_tag = host_busy ? h_wr_cmd_tag : d_wr_cmd_tag;
    assign h_wr_cmd_ready = host_busy && b_wr_cmd_ready;
    assign d_wr_cmd_ready = !host_busy && b_wr_cmd_ready;
    assign b_wr_data_valid = host_busy ? h_wr_data_valid : d_wr_data_valid;
    assign b_wr_data = host_busy ? h_wr_data : d_wr_data;
    assign b_wr_data_strb = host_busy ? h_wr_data_strb : d_wr_data_strb;
    assign h_wr_data_ready = host_busy && b_wr_data_ready;
    assign d_wr_data_ready = !host_busy && b_wr_data_ready;
    assign h_wr_done_valid = host_busy && b_wr_done_valid;
    assign d_wr_done_valid = !host_busy && b_wr_done_valid;
    assign b_wr_done_ready = host_busy ? h_wr_done_ready : d_wr_done_ready;

    gemm_packet_transport transport (.*);
    gemm_ddr_control #(.ID(ID), .VERSION(VERSION), .DDR_BYTES(DDR_BYTES)) control (
        .job_busy(busy), .job_ready(ready), .watchdog_limit(cfg_watchdog),
        .mem_fatal(control_mem_fatal), .mem_fatal_code(control_mem_fatal_code),
        .rd_cmd_valid(h_rd_cmd_valid), .rd_cmd_ready(h_rd_cmd_ready),
        .rd_cmd_addr(h_rd_cmd_addr), .rd_cmd_beats(h_rd_cmd_beats), .rd_cmd_tag(h_rd_cmd_tag),
        .rd_data_valid(h_rd_data_valid), .rd_data_ready(h_rd_data_ready),
        .rd_data(b_rd_data), .rd_data_index(b_rd_data_index), .rd_data_last(b_rd_data_last), .rd_data_tag(b_rd_data_tag),
        .rd_done_valid(h_rd_done_valid), .rd_done_ready(h_rd_done_ready),
        .rd_done_status(b_rd_done_status), .rd_done_tag(b_rd_done_tag),
        .wr_cmd_valid(h_wr_cmd_valid), .wr_cmd_ready(h_wr_cmd_ready),
        .wr_cmd_addr(h_wr_cmd_addr), .wr_cmd_beats(h_wr_cmd_beats), .wr_cmd_tag(h_wr_cmd_tag),
        .wr_data_valid(h_wr_data_valid), .wr_data_ready(h_wr_data_ready),
        .wr_data(h_wr_data), .wr_data_strb(h_wr_data_strb),
        .wr_done_valid(h_wr_done_valid), .wr_done_ready(h_wr_done_ready),
        .wr_done_status(b_wr_done_status), .wr_done_tag(b_wr_done_tag), .*
    );
    gemm_ddr_registers #(.P(P), .T(T), .BUILD_ID(BUILD_ID), .CORE_HZ(CORE_HZ), .ID(ID), .VERSION(VERSION)) registers (
        .req_valid(reg_req_valid), .req_ready(reg_req_ready), .req_addr(reg_req_addr),
        .req_write(reg_req_write), .req_wdata(reg_req_wdata), .req_wstrb(reg_req_wstrb),
        .rsp_valid(reg_rsp_valid), .rsp_ready(reg_rsp_ready),
        .rsp_rdata(reg_rsp_rdata), .rsp_status(reg_rsp_status),
        .job_ready(ready), .job_busy(busy || reg_busy_guard), .job_done(done), .job_error(error), .*
    );
    generate if (ENABLE_OVERLAP) begin : overlap_path
        // Public job safety/counters surround the same tagged scheduler used
        // by the portable ownership tests. Host AXI arbitration stays shared.
        gemm_ddr_overlap_job #(.P(P), .T(T), .DDR_BYTES(DDR_BYTES)) job (
            .start_valid(job_start_valid), .start_ready(job_start_ready),
            .start_rsp_valid(job_start_rsp_valid), .start_rsp_ready(job_start_rsp_ready),
            .start_rsp_status(job_start_rsp_status), .clear_status(job_clear_status),
            .clear_status_code(job_clear_status_code), .compute_progress(compute_busy),
            .axi_rvalid(m_axi_rvalid && !host_busy), .axi_rready(m_axi_rready),
            .axi_wvalid(m_axi_wvalid && !host_busy), .axi_wready(m_axi_wready), .axi_wstrb(m_axi_wstrb),
            .axi_bvalid(m_axi_bvalid && !host_busy), .axi_bready(m_axi_bready), .axi_bresp(m_axi_bresp),
            .mem_progress(mem_progress && !host_busy), .*
        );
        gemm_tile_dma_duplex #(.P(P), .T(T), .DDR_BYTES(DDR_BYTES), .READ_SLOTS(READ_SLOTS)) dma (
            .busy(dma_busy), .fatal(dma_fatal), .fatal_code(dma_fatal_code),
            .rd_cmd_valid(d_rd_cmd_valid), .rd_cmd_ready(d_rd_cmd_ready),
            .rd_cmd_addr(d_rd_cmd_addr), .rd_cmd_beats(d_rd_cmd_beats), .rd_cmd_tag(d_rd_cmd_tag),
            .rd_data_valid(d_rd_data_valid), .rd_data_ready(d_rd_data_ready),
            .rd_data(b_rd_data), .rd_data_index(b_rd_data_index), .rd_data_last(b_rd_data_last), .rd_data_tag(b_rd_data_tag),
            .rd_done_valid(d_rd_done_valid), .rd_done_ready(d_rd_done_ready),
            .rd_done_status(b_rd_done_status), .rd_done_tag(b_rd_done_tag),
            .wr_cmd_valid(d_wr_cmd_valid), .wr_cmd_ready(d_wr_cmd_ready),
            .wr_cmd_addr(d_wr_cmd_addr), .wr_cmd_beats(d_wr_cmd_beats), .wr_cmd_tag(d_wr_cmd_tag),
            .wr_data_valid(d_wr_data_valid), .wr_data_ready(d_wr_data_ready),
            .wr_data(d_wr_data), .wr_data_strb(d_wr_data_strb),
            .wr_done_valid(d_wr_done_valid), .wr_done_ready(d_wr_done_ready),
            .wr_done_status(b_wr_done_status), .wr_done_tag(b_wr_done_tag), .*
        );
        // Serial descriptor aliases are only used by the shared ownership
        // assertion and retain defined values in this configuration.
        assign dma_req_valid = load_req_valid || store_req_valid;
        assign dma_req_ready = 1'b0;
        assign dma_req_write = 1'b0;
        assign dma_req_bt = 1'b0;
        assign dma_req_buf = 1'b0;
        assign dma_req_base = 32'd0;
        assign dma_req_stride = 32'd0;
        assign dma_req_rows = 6'd0;
        assign dma_req_row_bytes = 9'd0;
        assign dma_done_valid = 1'b0;
        assign dma_done_ready = 1'b0;
        assign dma_done_status = 16'd0;
    end else begin : serial_path
    gemm_ddr_job #(.P(P), .T(T), .DDR_BYTES(DDR_BYTES)) job (
        .start_valid(job_start_valid), .start_ready(job_start_ready),
        .start_rsp_valid(job_start_rsp_valid), .start_rsp_ready(job_start_rsp_ready),
        .start_rsp_status(job_start_rsp_status), .clear_status(job_clear_status),
        .clear_status_code(job_clear_status_code), .compute_progress(compute_busy),
        .axi_rvalid(m_axi_rvalid && !host_busy), .axi_rready(m_axi_rready),
        .axi_wvalid(m_axi_wvalid && !host_busy), .axi_wready(m_axi_wready), .axi_wstrb(m_axi_wstrb),
        .axi_bvalid(m_axi_bvalid && !host_busy), .axi_bready(m_axi_bready), .axi_bresp(m_axi_bresp),
        .mem_progress(mem_progress && !host_busy), .*
    );
    gemm_tile_dma #(.P(P), .T(T), .DDR_BYTES(DDR_BYTES), .READ_SLOTS(READ_SLOTS)) dma (
        .req_valid(dma_req_valid), .req_ready(dma_req_ready),
        .req_write(dma_req_write), .req_bt(dma_req_bt), .req_buf(dma_req_buf),
        .req_base(dma_req_base), .req_stride(dma_req_stride),
        .req_rows(dma_req_rows), .req_row_bytes(dma_req_row_bytes),
        .busy(dma_busy), .done_valid(dma_done_valid), .done_ready(dma_done_ready),
        .done_status(dma_done_status), .fatal(dma_fatal), .fatal_code(dma_fatal_code),
        .rd_cmd_valid(d_rd_cmd_valid), .rd_cmd_ready(d_rd_cmd_ready),
        .rd_cmd_addr(d_rd_cmd_addr), .rd_cmd_beats(d_rd_cmd_beats), .rd_cmd_tag(d_rd_cmd_tag),
        .rd_data_valid(d_rd_data_valid), .rd_data_ready(d_rd_data_ready),
        .rd_data(b_rd_data), .rd_data_index(b_rd_data_index), .rd_data_last(b_rd_data_last), .rd_data_tag(b_rd_data_tag),
        .rd_done_valid(d_rd_done_valid), .rd_done_ready(d_rd_done_ready),
        .rd_done_status(b_rd_done_status), .rd_done_tag(b_rd_done_tag),
        .wr_cmd_valid(d_wr_cmd_valid), .wr_cmd_ready(d_wr_cmd_ready),
        .wr_cmd_addr(d_wr_cmd_addr), .wr_cmd_beats(d_wr_cmd_beats), .wr_cmd_tag(d_wr_cmd_tag),
        .wr_data_valid(d_wr_data_valid), .wr_data_ready(d_wr_data_ready),
        .wr_data(d_wr_data), .wr_data_strb(d_wr_data_strb),
        .wr_done_valid(d_wr_done_valid), .wr_done_ready(d_wr_done_ready),
        .wr_done_status(b_wr_done_status), .wr_done_tag(b_wr_done_tag), .*
    );
    end endgenerate
    gemm_tile_engine #(.P(P), .T(T), .CONCURRENT_PORTS(ENABLE_OVERLAP)) tile (
        .start(compute_start), .start_ready(compute_ready),
        .input_buf(compute_input_buf), .output_buf(compute_output_buf),
        .m(compute_m), .n(compute_n), .k(compute_k), .busy(compute_busy),
        .done(compute_done), .cmd_error(compute_error), .compute_cycles(compute_cycles_in),
        .job_cycles(), .microtiles(), .result_valid(), .*
    );
    gemm_axi_burst #(.READ_SLOTS(READ_SLOTS)) burst (
        .rd_cancel(reset_required || host_fatal || dma_fatal),
        .rd_cmd_valid(b_rd_cmd_valid), .rd_cmd_ready(b_rd_cmd_ready),
        .rd_cmd_addr(b_rd_cmd_addr), .rd_cmd_beats(b_rd_cmd_beats), .rd_cmd_tag(b_rd_cmd_tag),
        .rd_data_valid(b_rd_data_valid), .rd_data_ready(b_rd_data_ready),
        .rd_data(b_rd_data), .rd_data_index(b_rd_data_index), .rd_data_last(b_rd_data_last), .rd_data_tag(b_rd_data_tag),
        .rd_done_valid(b_rd_done_valid), .rd_done_ready(b_rd_done_ready),
        .rd_done_status(b_rd_done_status), .rd_done_tag(b_rd_done_tag),
        .wr_cmd_valid(b_wr_cmd_valid), .wr_cmd_ready(b_wr_cmd_ready),
        .wr_cmd_addr(b_wr_cmd_addr), .wr_cmd_beats(b_wr_cmd_beats), .wr_cmd_tag(b_wr_cmd_tag),
        .wr_data_valid(b_wr_data_valid), .wr_data_ready(b_wr_data_ready),
        .wr_data(b_wr_data), .wr_data_strb(b_wr_data_strb),
        .wr_done_valid(b_wr_done_valid), .wr_done_ready(b_wr_done_ready),
        .wr_done_status(b_wr_done_status), .wr_done_tag(b_wr_done_tag),
        .fatal(burst_fatal), .fatal_code(burst_fatal_code), .progress(mem_progress),
        .local_idle(burst_local_idle), .*
    );

`ifndef SYNTHESIS
    logic previous_host;
    always @(posedge clk) begin
        if (rst) previous_host <= 0;
        else begin
            if (host_busy && (dma_req_valid || compute_start || d_rd_cmd_valid || d_wr_cmd_valid))
                $fatal(1,"Host and job both offered memory work");
            if (host_busy != previous_host && (!axi_quiescent || b_rd_data_valid || b_rd_done_valid || b_wr_done_valid))
                $fatal(1,"DDR ownership changed with outstanding traffic");
            // Free command credit or AXI quiescence can still coexist with
            // buffered data, held terminals or a write being collected.
            if (host_busy != previous_host && !burst_local_idle)
                $fatal(1,"DDR ownership changed before local burst completion");
            previous_host <= host_busy;
        end
    end
`endif
endmodule
