`timescale 1ns/1ps
// Internal scheduler with production compute/DMA; gates and fault muxes are test-only.
module tile_scheduler_harness #(
    parameter integer P=4,T=8,READ_SLOTS=1,DIM_W=$clog2(T+1)
) (
    input wire clk,rst,job_valid,
    output wire job_ready,busy,done,fatal,
    output wire [15:0] fatal_code,
    input wire [31:0] cfg_m,cfg_n,cfg_k,cfg_mode,
    input wire [31:0] cfg_a_base,cfg_bt_base,cfg_c_base,
    input wire [31:0] cfg_a_stride,cfg_bt_stride,cfg_c_stride,
    input wire allow_load,allow_read,allow_response,allow_rd_cmd,allow_rd_done,allow_wr_cmd,allow_wr_data,
    input wire allow_load_done,allow_store_done,allow_compute,
    input wire inject_response_error,inject_response_strb,inject_rd_tag,inject_rd_index,inject_rd_last,
    input wire inject_wr_done,inject_spurious_b,inject_mem_fatal,inject_compute_error,
    input wire inject_scheduler_fatal,
    input wire [15:0] inject_mem_code,inject_scheduler_code,
    input wire [1:0] inject_rresp,inject_bresp,
    input wire hold_local_idle,hold_axi_quiescent,
    output wire axi_quiescent,local_idle,
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
    wire load_req_valid,load_req_ready,load_req_bt,load_req_buf;
    wire [31:0] load_req_base,load_req_stride;
    wire [5:0] load_req_rows;
    wire [8:0] load_req_row_bytes;
    wire load_busy,load_done_valid,load_done_ready,scheduler_load_done_ready;
    wire [15:0] load_done_status;
    wire store_req_valid,store_req_ready,store_req_buf;
    wire [31:0] store_req_base,store_req_stride;
    wire [5:0] store_req_rows;
    wire [8:0] store_req_row_bytes;
    wire store_busy,store_done_valid,store_done_ready,scheduler_store_done_ready;
    wire [15:0] store_done_status;
    wire dma_busy,dma_fatal;
    wire [15:0] dma_fatal_code;
    wire compute_start,compute_ready,raw_compute_ready,compute_busy,compute_done,compute_error,raw_compute_error;
    wire compute_input_buf,compute_output_buf;
    wire [DIM_W-1:0] compute_m,compute_n;
    wire [8:0] compute_k;
    wire [63:0] local_job_cycles;
    wire mem_fatal = inject_scheduler_fatal || dma_fatal;
    wire [15:0] mem_fatal_code = inject_scheduler_fatal ? inject_scheduler_code : dma_fatal_code;
    // Scheduler fault output is registered; do not feed its immediate stop back.
    wire feedback_fatal = fatal || inject_mem_fatal;
    wire [15:0] feedback_code = fatal ? fatal_code : inject_mem_code;
    assign load_done_ready = scheduler_load_done_ready && allow_load_done;
    assign store_done_ready = scheduler_store_done_ready && allow_store_done;
    assign compute_ready = raw_compute_ready && allow_compute;
    assign compute_error = raw_compute_error || inject_compute_error;

    gemm_tile_scheduler #(.P(P),.T(T)) scheduler (
        .load_done_valid(load_done_valid && allow_load_done), .load_done_ready(scheduler_load_done_ready),
        .store_done_valid(store_done_valid && allow_store_done), .store_done_ready(scheduler_store_done_ready),
        .burst_local_idle(local_idle && !hold_local_idle),
        .axi_quiescent(axi_quiescent && !hold_axi_quiescent),
        .compute_complete(), .compute_inflight(), .input_waiting(), .detected_fault_code(), .detected_fault(), .*
    );
    tile_dma_duplex_harness #(.P(P),.T(T),.READ_SLOTS(READ_SLOTS)) datapath (
        .start(compute_start && allow_compute),.start_ready(raw_compute_ready),
        .input_buf(compute_input_buf),.output_buf(compute_output_buf),
        .m(compute_m),.n(compute_n),.k(compute_k),
        .busy(dma_busy),.fatal(dma_fatal),.fatal_code(dma_fatal_code),
        .compute_cmd_error(raw_compute_error),.job_cycles(local_job_cycles),
        .inject_mem_fatal(feedback_fatal),.inject_mem_code(feedback_code), .*
    );
endmodule
