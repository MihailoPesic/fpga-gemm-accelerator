`timescale 1ns/1ps
// Two A/BT buffer sets. No data reset: ownership belongs to the caller.
// q -> bank=q%P; word=buf*(T/P)*32+(q/P)*32+word_index.
// Synchronous reads return one 64-bit word per bank after the request edge.
module gemm_operand_banks #(
    parameter integer P = 4,
    parameter integer T = 32,
    parameter integer Q_W = $clog2(T),
    parameter integer GROUP_W = (T/P > 1) ? $clog2(T/P) : 1,
    parameter integer ADDR_W = $clog2(2*(T/P)*32)
) (
    input wire clk,
    input wire wr_en, wr_bt, wr_buf,
    input wire [Q_W-1:0] wr_q,
    input wire [4:0] wr_word,
    input wire [63:0] wr_data,
    input wire rd_en, rd_buf,
    input wire [GROUP_W-1:0] rd_a_group, rd_bt_group,
    input wire [4:0] rd_word,
    output wire [P*64-1:0] a_words, bt_words
);
    localparam integer DEPTH = 2*(T/P)*32;
    wire [ADDR_W-1:0] wa = ADDR_W'(wr_buf*(T/P)*32 + (wr_q/P)*32 + wr_word);
    wire [ADDR_W-1:0] aa = ADDR_W'(rd_buf*(T/P)*32 + rd_a_group*32 + rd_word);
    wire [ADDR_W-1:0] ba = ADDR_W'(rd_buf*(T/P)*32 + rd_bt_group*32 + rd_word);

    for (genvar bank=0; bank<P; bank=bank+1) begin : banks
        (* ram_style = "block" *) logic [63:0] a_mem [0:DEPTH-1];
        (* ram_style = "block" *) logic [63:0] bt_mem [0:DEPTH-1];
        logic [63:0] a_read, bt_read;
        always_ff @(posedge clk) begin
            if (wr_en && wr_q % P == bank) begin
                if (wr_bt) bt_mem[wa] <= wr_data;
                else a_mem[wa] <= wr_data;
            end
        end
        always_ff @(posedge clk) begin
            if (rd_en) begin
                a_read <= a_mem[aa];
                bt_read <= bt_mem[ba];
            end
        end
        assign a_words[bank*64 +: 64] = a_read;
        assign bt_words[bank*64 +: 64] = bt_read;
    end
endmodule
