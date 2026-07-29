`timescale 1ns / 1ps
//=============================================================================
// uart_tx -- 8N1 transmitter
//
// Simpler than the receiver: we own the timing, so there is no sampling and no
// synchroniser. Load {stop, data[7:0], start} into a 10-bit shift register and
// walk it out LSB first, one bit every DIV cycles.
//=============================================================================

module uart_tx #(
  parameter int CLK_HZ = 50_000_000,      // ui_clk
  parameter int BAUD   = 921_600
)(
  input  logic       clk,
  input  logic       rst,                 // synchronous, active high
  input  logic [7:0] data,
  input  logic       send,                // pulse one cycle; ignored while busy
  output logic       busy,
  output logic       tx_pin
);

  localparam int DIV = CLK_HZ / BAUD;     // 54 @ 50 MHz / 921600
  localparam int TW  = $clog2(DIV);

  logic [9:0]    sh;
  logic [3:0]    bitcnt;
  logic [TW-1:0] tick;

  always_ff @(posedge clk) begin
    if (rst) begin
      busy   <= 1'b0;
      tick   <= '0;
      bitcnt <= '0;
      sh     <= '1;                       // all ones = idle line
    end else if (!busy) begin
      if (send) begin
        sh     <= {1'b1, data, 1'b0};     // stop bit, data, start bit
        bitcnt <= 4'd10;
        tick   <= '0;
        busy   <= 1'b1;
      end
    end else begin
      if (tick == TW'(DIV - 1)) begin
        tick   <= '0;
        sh     <= {1'b1, sh[9:1]};        // shift right, feed idle-high in
        bitcnt <= bitcnt - 1'b1;
        if (bitcnt == 4'd1) busy <= 1'b0; // 10 bits sent
      end else tick <= tick + 1'b1;
    end
  end

  // Idle high; while busy the line is the bottom of the shift register.
  assign tx_pin = busy ? sh[0] : 1'b1;

endmodule
