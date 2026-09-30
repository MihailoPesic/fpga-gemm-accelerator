`timescale 1ns/1ps
// Byte PHY, framed transport, and the serial BRAM preview controller.
module gemm_preview_uart #(
    parameter integer CORE_HZ = 100_000_000,
    parameter integer BAUD = 115_200,
    parameter logic [31:0] BUILD_ID = 32'd0
) (
    input wire clk, rst, rx,
    output wire tx, busy, done, error
);
    wire [7:0] rx_data, tx_data;
    wire rx_valid, rx_error, tx_valid, tx_ready, tx_busy;
    wire req_valid, req_ready, rsp_valid, rsp_ready;
    wire [7:0] req_opcode;
    wire [8:0] req_length, rsp_length;
    wire [2047:0] req_payload;
    wire [15:0] rsp_status;
    wire [1919:0] rsp_payload;

    uart_rx #(.CLK_HZ(CORE_HZ), .BAUD(BAUD)) receiver (
        .clk(clk), .rst(rst), .rx_pin(rx), .data(rx_data),
        .valid(rx_valid), .frame_err(rx_error)
    );
    // Combinational ready/send: UART and transport accept the same edge.
    // Registering send here would require another pending-byte interlock.
    assign tx_ready = !rst && !tx_busy;
    uart_tx #(.CLK_HZ(CORE_HZ), .BAUD(BAUD)) transmitter (
        .clk(clk), .rst(rst), .data(tx_data), .send(tx_valid && tx_ready),
        .busy(tx_busy), .tx_pin(tx)
    );
    gemm_packet_transport transport (
        .clk(clk), .rst(rst), .rx_valid(rx_valid), .rx_error(rx_error), .rx_data(rx_data),
        .tx_valid(tx_valid), .tx_ready(tx_ready), .tx_data(tx_data),
        .req_valid(req_valid), .req_ready(req_ready), .req_opcode(req_opcode),
        .req_length(req_length), .req_payload(req_payload),
        .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_status(rsp_status),
        .rsp_length(rsp_length), .rsp_payload(rsp_payload)
    );
    gemm_preview_controller #(.BUILD_ID(BUILD_ID), .CORE_HZ(CORE_HZ)) controller (
        .clk(clk), .rst(rst), .req_valid(req_valid), .req_ready(req_ready),
        .req_opcode(req_opcode), .req_length(req_length), .req_payload(req_payload),
        .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_status(rsp_status),
        .rsp_length(rsp_length), .rsp_payload(rsp_payload),
        .busy(busy), .done(done), .error(error)
    );
endmodule
