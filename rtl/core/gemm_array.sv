`timescale 1ns/1ps
// Output-stationary mesh. Inputs at the left/top edges MUST already be skewed.
// Lane q is bits [8*q +: 8]; sum(r,c) is bits [32*(r*P+c) +: 32].
module gemm_array #(parameter integer P = 4) (
    input wire clk, clear,
    input wire [P*8-1:0] a_edge, b_edge,
    input wire [P-1:0] a_valid, b_valid,
    output wire [P*P*32-1:0] sums
`ifndef SYNTHESIS
    , input wire [P*8-1:0] a_tags, b_tags
`endif
);
    wire signed [7:0] a [0:P-1][0:P];
    wire signed [7:0] b [0:P][0:P-1];
    wire av [0:P-1][0:P];
    wire bv [0:P][0:P-1];
`ifndef SYNTHESIS
    wire [7:0] atag [0:P-1][0:P];
    wire [7:0] btag [0:P][0:P-1];
`endif
    for (genvar q=0; q<P; q=q+1) begin : edges
        assign a[q][0] = a_edge[q*8 +: 8];
        assign b[0][q] = b_edge[q*8 +: 8];
        assign av[q][0] = a_valid[q];
        assign bv[0][q] = b_valid[q];
`ifndef SYNTHESIS
        assign atag[q][0] = a_tags[q*8 +: 8];
        assign btag[0][q] = b_tags[q*8 +: 8];
`endif
    end
    for (genvar r=0; r<P; r=r+1) begin : rows
        for (genvar c=0; c<P; c=c+1) begin : cols
            gemm_pe pe (
                .clk(clk), .clear(clear), .a(a[r][c]), .b(b[r][c]),
                .a_valid(av[r][c]), .b_valid(bv[r][c]),
                .a_out(a[r][c+1]), .b_out(b[r+1][c]),
                .a_valid_out(av[r][c+1]), .b_valid_out(bv[r+1][c]),
                .sum(sums[(r*P+c)*32 +: 32])
            );
`ifndef SYNTHESIS
            logic [7:0] atag_q, btag_q;
            assign atag[r][c+1] = atag_q;
            assign btag[r+1][c] = btag_q;
            always @(posedge clk) begin
                atag_q <= atag[r][c];
                btag_q <= btag[r][c];
                if (!clear && av[r][c] && bv[r][c])
                    if (atag[r][c] !== btag[r][c])
                        $fatal(1, "Reduction tag mismatch at PE(%0d,%0d)", r, c);
            end
`endif
        end
    end
endmodule
