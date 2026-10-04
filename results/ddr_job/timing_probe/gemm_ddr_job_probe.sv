`timescale 1ns/1ps
// Serial DDR GEMM job controller. Validation precedes the accepted-job epoch;
// no DMA or compute request is made for an invalid descriptor. The surrounding
// platform must coordinate reset across this controller, DMA, AXI and MIG.
module gemm_ddr_job_probe #(
    parameter integer P=4, T=32, DIM_W=$clog2(T+1),
    parameter logic [32:0] DDR_BYTES=33'd134217728
) (
    input wire clk, rst, ddr_ready, host_busy,
    input wire start_valid,
    output wire start_ready,
    input wire [31:0] cfg_job_id, cfg_m, cfg_n, cfg_k,
    input wire [31:0] cfg_a_base, cfg_bt_base, cfg_c_base,
    input wire [31:0] cfg_a_stride, cfg_bt_stride, cfg_c_stride,
    input wire [31:0] cfg_mode, cfg_watchdog,
    output logic start_rsp_valid,
    input wire start_rsp_ready,
    output logic [15:0] start_rsp_status,
    output logic job_accepted,
    input wire clear_status,
    output wire [15:0] clear_status_code,
    output wire ready, busy,
    output logic done, error, reset_required,
    output logic [15:0] error_code,
    output logic [31:0] last_job_id,
    output logic [63:0] job_cycles, compute_cycles, read_beats, write_beats,
    output logic [63:0] write_valid_bytes, input_wait_cycles, read_stall_cycles, write_stall_cycles,
    output wire dma_req_valid,
    input wire dma_req_ready,
    output wire dma_req_write, dma_req_bt, dma_req_buf,
    output logic [31:0] dma_req_base, dma_req_stride,
    output wire [5:0] dma_req_rows,
    output wire [8:0] dma_req_row_bytes,
    input wire dma_busy, dma_done_valid,
    input wire [15:0] dma_done_status,
    output wire dma_done_ready,
    input wire dma_fatal,
    input wire [15:0] dma_fatal_code,
    output wire compute_start,
    input wire compute_ready, compute_busy, compute_done, compute_error, compute_progress,
    input wire [63:0] compute_cycles_in,
    output wire compute_input_buf, compute_output_buf,
    output wire [DIM_W-1:0] compute_m, compute_n,
    output wire [8:0] compute_k,
    input wire axi_rvalid, axi_rready, axi_wvalid, axi_wready,
    input wire [7:0] axi_wstrb,
    input wire axi_bvalid, axi_bready,
    input wire [1:0] axi_bresp,
    input wire axi_quiescent, mem_progress
);

    wire core_clk;
    BUFG clock_buffer (.I(clk), .O(core_clk));
    gemm_ddr_job #(.P(P), .T(T), .DIM_W(DIM_W), .DDR_BYTES(DDR_BYTES))
        controller (.clk(core_clk), .*);
endmodule
