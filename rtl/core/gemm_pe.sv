`timescale 1ns/1ps
// Signed INT8 multiply, registered product, then signed INT32 accumulate.
// clear discards an in-flight product as well as the accumulator contents.
module gemm_pe (
    input  wire clk, clear,
    input  wire signed [7:0] a, b,
    input  wire a_valid, b_valid,
    output logic signed [7:0] a_out, b_out,
    output logic a_valid_out, b_valid_out,
    output logic signed [31:0] sum
);
    // A signed 32-bit destination sign-extends the signed multiplication.
    // Keeping product and accumulator the same width permits Vivado to pack
    // both stages into ONE DSP48E1. Resource checks enforce this intent.
    (* use_dsp = "yes" *) logic signed [31:0] product;
    logic product_valid;

    always_ff @(posedge clk) begin
        a_out <= a;
        b_out <= b;
        a_valid_out <= !clear && a_valid;
        b_valid_out <= !clear && b_valid;
        product <= a * b;
        product_valid <= !clear && a_valid && b_valid;
        if (clear)
            sum <= '0;
        else if (product_valid)
            sum <= sum + product;
    end
endmodule
