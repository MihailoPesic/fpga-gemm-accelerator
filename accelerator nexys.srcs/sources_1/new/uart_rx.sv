`timescale 1ns / 1ps
//=============================================================================
// uart_rx -- 8N1 receiver
//
// Samples at the CENTRE of each bit, not its edge. Sequence after a falling
// edge on an idle-high line:
//
//   start edge --+--- DIV/2 ---+--- DIV ---+--- DIV ---+ ... +--- DIV ---+
//                |             |           |                 |
//            (detected)   centre of    centre of         centre of
//                         start bit     bit 0             stop bit
//
// The DIV/2 wait does two jobs: it lands mid-start-bit so a glitch can be
// rejected, and it puts every subsequent DIV-spaced sample at a bit centre,
// where there is +/-50% of a bit period of margin against baud error.
//=============================================================================

module uart_rx #(
  parameter int CLK_HZ = 50_000_000,      // ui_clk
  parameter int BAUD   = 921_600
)(
  input  logic       clk,
  input  logic       rst,                 // synchronous, active high
  input  logic       rx_pin,
  output logic [7:0] data,
  output logic       valid,               // one-cycle strobe
  output logic       frame_err            // stop bit was not high -> baud wrong
);

  localparam int DIV      = CLK_HZ / BAUD;   // 54 @ 50 MHz / 921600
  localparam int DIV_HALF = DIV / 2;         // 27
  localparam int TW       = $clog2(DIV);

  // rx_pin is asynchronous to clk. Two flops before any logic touches it, or
  // metastability produces rare, unreproducible corruption.
  (* ASYNC_REG = "TRUE" *) logic [2:0] sync;
  always_ff @(posedge clk)
    if (rst) sync <= 3'b111;               // line idles high
    else     sync <= {sync[1:0], rx_pin};
  wire rx_s = sync[2];

  typedef enum logic [1:0] { IDLE, START, DATA, STOP } state_t;
  state_t        state;
  logic [TW-1:0] tick;
  logic [2:0]    bitcnt;
  logic [7:0]    sh;

  always_ff @(posedge clk) begin
    valid <= 1'b0;                          // default: strobe is one cycle

    if (rst) begin
      state     <= IDLE;
      tick      <= '0;
      bitcnt    <= '0;
      frame_err <= 1'b0;
    end else begin
      case (state)

        IDLE:
          if (!rx_s) begin                  // falling edge: possible start bit
            tick  <= '0;
            state <= START;
          end

        START:
          if (tick == TW'(DIV_HALF - 1)) begin
            if (!rx_s) begin                // still low at centre -> genuine
              tick   <= '0;
              bitcnt <= '0;
              state  <= DATA;
            end else begin
              state  <= IDLE;               // glitch, not a start bit
            end
          end else tick <= tick + 1'b1;

        DATA:
          if (tick == TW'(DIV - 1)) begin
            tick <= '0;
            sh   <= {rx_s, sh[7:1]};        // LSB first: shift right, new bit at MSB
            if (bitcnt == 3'd7) state  <= STOP;
            else                bitcnt <= bitcnt + 1'b1;
          end else tick <= tick + 1'b1;

        STOP:
          if (tick == TW'(DIV - 1)) begin
            data      <= sh;
            frame_err <= ~rx_s;             // must be high in 8N1
            valid     <= 1'b1;
            state     <= IDLE;
          end else tick <= tick + 1'b1;

      endcase
    end
  end

endmodule
