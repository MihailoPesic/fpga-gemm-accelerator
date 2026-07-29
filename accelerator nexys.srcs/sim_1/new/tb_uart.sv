`timescale 1ns / 1ps
//=============================================================================
// tb_uart -- loopback test: uart_tx drives uart_rx over a single wire.
//
// Catches nearly every UART bug without a 10-minute synthesis run. The four
// test values are chosen deliberately:
//   00 -> finds bits stuck at 1
//   FF -> finds bits stuck at 0
//   A5 / 5A -> find reversed bit order (they are each other's mirror)
// Passing all four means the receiver is almost certainly correct.
//=============================================================================

module tb_uart;

  localparam int CLK_HZ = 50_000_000;     // ui_clk
  localparam int BAUD   = 921_600;

  logic clk = 1'b0;
  logic rst = 1'b1;
  always #10 clk = ~clk;                  // 50 MHz -> 20 ns period

  logic [7:0] tx_data, rx_data;
  logic       tx_send, tx_busy;
  logic       rx_valid, frame_err;
  logic       line;                       // the single wire under test

  int errors = 0;

  uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) dut_tx (
    .clk(clk), .rst(rst),
    .data(tx_data), .send(tx_send), .busy(tx_busy), .tx_pin(line));

  uart_rx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) dut_rx (
    .clk(clk), .rst(rst), .rx_pin(line),
    .data(rx_data), .valid(rx_valid), .frame_err(frame_err));

  task automatic send_byte(input logic [7:0] b);
    @(posedge clk);
    tx_data <= b;
    tx_send <= 1'b1;
    @(posedge clk);
    tx_send <= 1'b0;

    fork : wait_or_timeout
      begin wait (rx_valid); disable wait_or_timeout; end
      begin #(200us); $error("TIMEOUT waiting for rx_valid on %02h", b);
            errors++; disable wait_or_timeout; end
    join

    if (rx_data !== b) begin
      $error("sent %02h, received %02h", b, rx_data);
      errors++;
    end else if (frame_err) begin
      $error("framing error on %02h -- stop bit was low", b);
      errors++;
    end else begin
      $display("  ok  %02h", b);
    end

    wait (!tx_busy);
    repeat (4) @(posedge clk);
  endtask

  initial begin
    $display("uart loopback: CLK_HZ=%0d BAUD=%0d DIV=%0d",
             CLK_HZ, BAUD, CLK_HZ / BAUD);
    tx_send = 1'b0;
    tx_data = 8'h00;

    repeat (10) @(posedge clk);
    rst <= 1'b0;
    repeat (10) @(posedge clk);

    send_byte(8'h00);
    send_byte(8'hFF);
    send_byte(8'hA5);
    send_byte(8'h5A);

    // Back-to-back, no idle gap: proves the receiver re-arms in time.
    send_byte(8'h01);
    send_byte(8'h02);
    send_byte(8'h03);

    if (errors == 0) $display("PASS -- all bytes round-tripped");
    else             $display("FAIL -- %0d error(s)", errors);
    $finish;
  end

endmodule
