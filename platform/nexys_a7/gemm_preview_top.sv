`timescale 1ns/1ps
// P4/T32 local-memory preview. No MIG or DDR pins are used by this build.
module gemm_preview_top #(
    parameter integer BAUD = 115_200,
    parameter logic [31:0] BUILD_ID = 32'd0
) (
    input wire CLK100MHZ, CPU_RESETN, UART_TXD_IN,
    output wire UART_RXD_OUT,
    output wire [3:0] LED
);
    wire input_clk, clk;
    IBUF clock_input (.I(CLK100MHZ), .O(input_clk));
    BUFG clock_buffer (.I(input_clk), .O(clk));

    // The oscillator is free-running: sample both edges of the button through
    // three flops. INIT holds startup reset. Synchronous assertion keeps BRAM
    // address/enable controls timed, including while abandoning an active job.
    (* ASYNC_REG = "TRUE" *) logic [2:0] reset_sync = 3'b111;
    always_ff @(posedge clk)
        reset_sync <= {reset_sync[1:0], !CPU_RESETN};
    wire rst = reset_sync[2];
    assign LED[0] = !rst;
    gemm_preview_uart #(.BAUD(BAUD), .BUILD_ID(BUILD_ID)) preview (
        .clk(clk), .rst(rst), .rx(UART_TXD_IN), .tx(UART_RXD_OUT),
        .busy(LED[1]), .done(LED[2]), .error(LED[3])
    );
endmodule
