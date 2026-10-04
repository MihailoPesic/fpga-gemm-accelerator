`timescale 1ns/1ps
// The production job controller drives the complete portable memory/compute path.
// Gates and injected faults are simulation-only; ordinary traffic uses real RTL.
module ddr_job_harness #(
    parameter integer P=4, T=8, DIM_W=$clog2(T+1), READ_SLOTS=1
) (
    input wire clk, rst, ddr_ready, host_busy,
    input wire start_valid,
    output wire start_ready,
    input wire [31:0] cfg_job_id, cfg_m, cfg_n, cfg_k,
    input wire [31:0] cfg_a_base, cfg_bt_base, cfg_c_base,
    input wire [31:0] cfg_a_stride, cfg_bt_stride, cfg_c_stride,
    input wire [31:0] cfg_mode, cfg_watchdog,
    output wire start_rsp_valid,
    input wire start_rsp_ready,
    output wire [15:0] start_rsp_status,
    output wire job_accepted,
    input wire clear_status,
    output wire [15:0] clear_status_code,
    output wire ready, busy, done, error, reset_required,
    output wire [15:0] error_code,
    output wire [31:0] last_job_id,
    output wire [63:0] job_cycles, compute_cycles, read_beats, write_beats,
    output wire [63:0] write_valid_bytes, input_wait_cycles, read_stall_cycles, write_stall_cycles,
    input wire allow_load, allow_read, allow_response, allow_rd_cmd, allow_wr_cmd, allow_wr_data,
    input wire allow_dma_done, inject_compute_error, suppress_progress,
    input wire inject_response_error, inject_response_strb,
    input wire inject_rd_tag, inject_rd_index, inject_rd_last,
    input wire inject_wr_done, inject_spurious_b,
    input wire inject_mem_fatal,
    input wire [15:0] inject_mem_code,
    input wire [1:0] inject_rresp, inject_bresp,
    output wire axi_quiescent,
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
    wire dma_req_valid, dma_req_ready, dma_req_write, dma_req_bt, dma_req_buf;
    wire [31:0] dma_req_base, dma_req_stride;
    wire [5:0] dma_req_rows;
    wire [8:0] dma_req_row_bytes;
    wire dma_busy, dma_done_valid, dma_done_ready, dma_fatal;
    wire [15:0] dma_done_status, dma_fatal_code;
    wire raw_dma_done_valid;
    wire compute_start, compute_ready, compute_busy, compute_done, compute_error;
    wire compute_progress, compute_input_buf, compute_output_buf;
    wire [DIM_W-1:0] compute_m, compute_n;
    wire [8:0] compute_k;
    wire [63:0] compute_cycles_in, local_job_cycles;
    wire raw_compute_error, mem_progress;
    wire feedback_fatal = reset_required || inject_mem_fatal;
    wire [15:0] feedback_code = reset_required ? error_code : inject_mem_code;
    assign compute_error = raw_compute_error || inject_compute_error;
    assign compute_progress = compute_busy && !suppress_progress;
    assign compute_cycles_in = datapath.tile.compute_cycles;
    assign mem_progress = datapath.burst.progress && !suppress_progress;
    assign dma_done_valid = raw_dma_done_valid && allow_dma_done;

    gemm_ddr_job #(.P(P), .T(T)) job (
        .axi_rvalid(m_axi_rvalid), .axi_rready(m_axi_rready),
        .axi_wvalid(m_axi_wvalid), .axi_wready(m_axi_wready), .axi_wstrb(m_axi_wstrb),
        .axi_bvalid(m_axi_bvalid), .axi_bready(m_axi_bready),
        .axi_bresp(inject_bresp != 0 ? inject_bresp : m_axi_bresp), .*
    );
    tile_dma_harness #(.P(P), .T(T), .READ_SLOTS(READ_SLOTS)) datapath (
        .req_valid(dma_req_valid), .req_ready(dma_req_ready),
        .req_write(dma_req_write), .req_bt(dma_req_bt), .req_buf(dma_req_buf),
        .req_base(dma_req_base), .req_stride(dma_req_stride),
        .req_rows(dma_req_rows), .req_row_bytes(dma_req_row_bytes),
        .busy(dma_busy), .done_valid(raw_dma_done_valid),
        .done_ready(dma_done_ready && allow_dma_done), .done_status(dma_done_status),
        .allow_rd_done(1'b1),
        .fatal(dma_fatal), .fatal_code(dma_fatal_code),
        .start(compute_start), .start_ready(compute_ready),
        .input_buf(compute_input_buf), .output_buf(compute_output_buf),
        .m(compute_m), .n(compute_n), .k(compute_k),
        .job_cycles(local_job_cycles), .compute_cmd_error(raw_compute_error),
        .inject_mem_fatal(feedback_fatal), .inject_mem_code(feedback_code), .*
    );
endmodule
