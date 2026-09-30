`timescale 1ns/1ps
// C[row,col]: bank=col%P, word=buf*(T*T/P)+row*(T/P)+col/P.
// Writes drain one PE row. Reads return two adjacent INT32s, little-endian.
// Both ports are synchronous. Ownership and tail strobes belong to the caller.
module gemm_result_banks #(
    parameter integer P = 4,
    parameter integer T = 32,
    parameter integer Q_W = $clog2(T),
    parameter integer BANK_W = $clog2(P),
    parameter integer GROUP_W = (T/P > 1) ? $clog2(T/P) : 1,
    parameter integer ADDR_W = $clog2(2*T*T/P)
) (
    input wire clk,
    input wire wr_en, wr_buf,
    input wire [Q_W-1:0] wr_row,
    input wire [GROUP_W-1:0] wr_group,
    input wire [P-1:0] wr_mask,
    input wire [P*32-1:0] wr_data,
    input wire rd_en, rd_buf,
    input wire [Q_W-1:0] rd_row,
    input wire [Q_W-2:0] rd_pair,
    output wire [63:0] rd_data
);
    localparam integer DEPTH = 2*T*T/P;
    wire [ADDR_W-1:0] wa = ADDR_W'(wr_buf*(T*T/P) + wr_row*(T/P) + wr_group);
    wire [ADDR_W-1:0] ra = ADDR_W'(rd_buf*(T*T/P) + rd_row*(T/P) + (rd_pair*2)/P);
    wire [BANK_W-1:0] read_bank = BANK_W'(rd_pair*2);
    logic [BANK_W-1:0] bank_q;
    wire [P*32-1:0] words;
    always_ff @(posedge clk) if (rd_en) bank_q <= read_bank;
    for (genvar bank=0; bank<P; bank=bank+1) begin : banks
        (* ram_style = "block" *) logic [31:0] mem [0:DEPTH-1];
        logic [31:0] data_q;
        always_ff @(posedge clk) begin
            if (wr_en && wr_mask[bank]) mem[wa] <= wr_data[bank*32 +: 32];
        end
        always_ff @(posedge clk) begin
            if (rd_en && (bank == read_bank || bank == read_bank+1)) data_q <= mem[ra];
        end
        assign words[bank*32 +: 32] = data_q;
    end
    assign rd_data = {words[(bank_q+1)*32 +: 32], words[bank_q*32 +: 32]};
endmodule
