`timescale 1ns/1ps
// Timing harness only: models neighboring synchronous producer/consumer
// registers. It is NOT a board wrapper or an extra stage in the core contract.
module gemm_core_timing_top #(
    parameter integer P=4,
    parameter integer CW=$clog2(P+1), RW=$clog2(P)
) (
    input wire clk, rst, start,
    input wire [8:0] k,
    input wire [CW-1:0] rows, cols,
    input wire [P*8-1:0] a_vector, bt_vector,
    output logic start_ready, busy, done, cmd_error,
    output logic feed_valid,
    output logic [7:0] feed_index,
    output logic drain_valid,
    output logic [RW-1:0] drain_row,
    output logic [P-1:0] drain_mask,
    output logic [P*32-1:0] drain_data
);
    logic rst_q, start_q;
    logic [8:0] k_q;
    logic [CW-1:0] rows_q, cols_q;
    logic [P*8-1:0] a_q, bt_q;
    wire ready_w, busy_w, done_w, error_w, feed_w, drain_w;
    wire [7:0] index_w;
    wire [RW-1:0] row_w;
    wire [P-1:0] mask_w;
    wire [P*32-1:0] data_w;
    always @(posedge clk) begin
        rst_q <= rst;
        start_q <= start;
        k_q <= k;
        rows_q <= rows;
        cols_q <= cols;
        a_q <= a_vector;
        bt_q <= bt_vector;
        start_ready <= ready_w;
        busy <= busy_w;
        done <= done_w;
        cmd_error <= error_w;
        feed_valid <= feed_w;
        feed_index <= index_w;
        drain_valid <= drain_w;
        drain_row <= row_w;
        drain_mask <= mask_w;
        drain_data <= data_w;
    end
    gemm_microtile #(.P(P)) core (
        .clk(clk), .rst(rst_q), .start(start_q), .k(k_q), .rows(rows_q), .cols(cols_q),
        .a_vector(a_q), .bt_vector(bt_q), .start_ready(ready_w), .busy(busy_w),
        .done(done_w), .cmd_error(error_w), .feed_valid(feed_w), .feed_index(index_w),
        .drain_valid(drain_w), .drain_row(row_w), .drain_mask(mask_w), .drain_data(data_w)
    );
endmodule
