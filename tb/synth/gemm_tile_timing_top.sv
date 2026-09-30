`timescale 1ns/1ps
// Synchronous producer/consumer registers for standalone engine timing.
module gemm_tile_timing_top #(
    parameter integer P=4, T=32,
    parameter integer QW=$clog2(T), DW=$clog2(T+1)
) (
    input wire clk, rst, load_valid, load_bt, load_buf,
    input wire [QW-1:0] load_q,
    input wire [4:0] load_word,
    input wire [63:0] load_data,
    input wire start, input_buf, output_buf,
    input wire [DW-1:0] m, n,
    input wire [8:0] k,
    input wire read_valid, read_buf,
    input wire [QW-1:0] read_row,
    input wire [QW-2:0] read_pair,
    input wire response_ready,
    output logic load_ready, start_ready, busy, done, cmd_error,
    output logic [1:0] result_valid,
    output logic [63:0] job_cycles, compute_cycles,
    output logic [31:0] microtiles,
    output logic read_ready, response_valid, response_error,
    output logic [63:0] response_data,
    output logic [7:0] response_strb
);
    // Route the real global clock inside the OOC harness. HD.CLK_SRC alone
    // estimates an external clock network and cannot demonstrate routed skew.
    wire core_clk;
    BUFG clock_buffer (.I(clk), .O(core_clk));
    logic rst_q, lv_q, lbt_q, lb_q, start_q, ib_q, ob_q, rv_q, rb_q, rr_q;
    logic [QW-1:0] lq_q, row_q;
    logic [QW-2:0] pair_q;
    logic [4:0] word_q;
    logic [63:0] data_q;
    logic [DW-1:0] m_q, n_q;
    logic [8:0] k_q;
    wire lr_w, sr_w, busy_w, done_w, error_w, ready_w, valid_w, rerror_w;
    wire [1:0] result_w;
    wire [63:0] job_w, compute_w, data_w;
    wire [31:0] tiles_w;
    wire [7:0] strb_w;
    always_ff @(posedge core_clk) begin
        {rst_q, lv_q, lbt_q, lb_q, lq_q, word_q, data_q, start_q, ib_q, ob_q,
         m_q, n_q, k_q, rv_q, rb_q, row_q, pair_q, rr_q} <=
        {rst, load_valid, load_bt, load_buf, load_q, load_word, load_data, start,
         input_buf, output_buf, m, n, k, read_valid, read_buf, read_row, read_pair, response_ready};
        {load_ready, start_ready, busy, done, cmd_error, result_valid, job_cycles,
         compute_cycles, microtiles, read_ready, response_valid, response_error,
         response_data, response_strb} <=
        {lr_w, sr_w, busy_w, done_w, error_w, result_w, job_w, compute_w, tiles_w,
         ready_w, valid_w, rerror_w, data_w, strb_w};
    end
    gemm_tile_engine #(.P(P), .T(T)) engine (
        .clk(core_clk), .rst(rst_q), .load_valid(lv_q), .load_ready(lr_w),
        .load_bt(lbt_q), .load_buf(lb_q), .load_q(lq_q), .load_word(word_q), .load_data(data_q),
        .start(start_q), .start_ready(sr_w), .input_buf(ib_q), .output_buf(ob_q),
        .m(m_q), .n(n_q), .k(k_q), .busy(busy_w), .done(done_w), .cmd_error(error_w),
        .result_valid(result_w), .job_cycles(job_w), .compute_cycles(compute_w), .microtiles(tiles_w),
        .read_valid(rv_q), .read_ready(ready_w), .read_buf(rb_q), .read_row(row_q), .read_pair(pair_q),
        .response_valid(valid_w), .response_ready(rr_q), .response_data(data_w),
        .response_strb(strb_w), .response_error(rerror_w)
    );
endmodule
