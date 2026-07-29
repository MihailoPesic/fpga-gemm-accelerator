`timescale 1ns / 1ps
//=============================================================================
// uart_ddr_bridge -- command processor: UART byte stream <-> DDR2 native port
//
// Protocol (multi-byte fields are big-endian):
//
//   0x01 PING                     -> 4 bytes  'N','X','A','7'
//   0x02 WRITE  addr[4] data[16]  -> 1 byte   0x5A
//   0x03 READ   addr[4]           -> 16 bytes
//
// addr is an app_addr value, not a byte address. One 128-bit transaction
// covers 16 bytes = 8 app_addr units, so consecutive words are 8 apart.
//
// One MIG beat per transaction: at a 4:1 PHY ratio with burst length 8, a
// single 128-bit app_wdf_data covers the whole DRAM burst, so app_wdf_end is
// asserted on that one beat.
//=============================================================================

module uart_ddr_bridge (
  input  logic         clk,               // ui_clk
  input  logic         rst,               // ui_clk_sync_rst
  input  logic         calib_done,

  // UART
  input  logic [7:0]   rx_data,
  input  logic         rx_valid,
  output logic [7:0]   tx_data,
  output logic         tx_send,
  input  logic         tx_busy,

  // MIG native user interface
  output logic [26:0]  app_addr,
  output logic [2:0]   app_cmd,
  output logic         app_en,
  input  logic         app_rdy,
  output logic [127:0] app_wdf_data,
  output logic [15:0]  app_wdf_mask,
  output logic         app_wdf_wren,
  output logic         app_wdf_end,
  input  logic         app_wdf_rdy,
  input  logic [127:0] app_rd_data,
  input  logic         app_rd_data_valid,

  output logic         busy_led
);

  localparam logic [2:0] CMD_WRITE = 3'd0;   // MIG opcodes
  localparam logic [2:0] CMD_READ  = 3'd1;

  localparam logic [7:0] OP_PING  = 8'h01;
  localparam logic [7:0] OP_WRITE = 8'h02;
  localparam logic [7:0] OP_READ  = 8'h03;

  typedef enum logic [3:0] {
    S_OPCODE, S_COLLECT, S_DISPATCH,
    S_WR, S_RD_CMD, S_RD_WAIT,
    S_RESP
  } state_t;

  state_t       state;
  logic [7:0]   opcode;
  logic [159:0] argbuf;                    // 20 bytes max (addr + 16 data)
  logic [4:0]   argcnt;                    // bytes still expected
  logic [127:0] resp;                      // shifted out MSB first
  logic [4:0]   respcnt;                   // bytes still to send

  // Write handshakes complete independently; track both.
  logic cmd_done, wdf_done;

  assign app_wdf_mask = 16'h0000;          // 0 = write every byte
  assign busy_led     = (state != S_OPCODE);

  always_ff @(posedge clk) begin
    tx_send <= 1'b0;                       // default: one-cycle pulse

    if (rst) begin
      state        <= S_OPCODE;
      app_en       <= 1'b0;
      app_wdf_wren <= 1'b0;
      app_wdf_end  <= 1'b0;
      argcnt       <= '0;
      respcnt      <= '0;
      cmd_done     <= 1'b0;
      wdf_done     <= 1'b0;
    end else begin
      case (state)

        //---------------------------------------------------------------
        S_OPCODE: begin
          if (rx_valid) begin
            opcode <= rx_data;
            case (rx_data)
              OP_PING:  begin
                          resp    <= {"N", "X", "A", "7", 96'd0};
                          respcnt <= 5'd4;
                          state   <= S_RESP;
                        end
              OP_WRITE: begin argcnt <= 5'd20; state <= S_COLLECT; end
              OP_READ:  begin argcnt <= 5'd4;  state <= S_COLLECT; end
              default:  ;                  // unknown opcode: ignore
            endcase
          end
        end

        //---------------------------------------------------------------
        S_COLLECT: begin
          if (rx_valid) begin
            argbuf <= {argbuf[151:0], rx_data};
            if (argcnt == 5'd1) state  <= S_DISPATCH;
            else                argcnt <= argcnt - 1'b1;
          end
        end

        //---------------------------------------------------------------
        S_DISPATCH: begin
          if (!calib_done) begin
            resp    <= {8'hEE, 120'd0};    // DDR2 not ready
            respcnt <= 5'd1;
            state   <= S_RESP;
          end else if (opcode == OP_WRITE) begin
            // argbuf = { addr[31:0], data[127:0] }
            app_addr     <= argbuf[154:128];
            app_cmd      <= CMD_WRITE;
            app_en       <= 1'b1;
            app_wdf_data <= argbuf[127:0];
            app_wdf_wren <= 1'b1;
            app_wdf_end  <= 1'b1;
            cmd_done     <= 1'b0;
            wdf_done     <= 1'b0;
            state        <= S_WR;
          end else begin
            app_addr <= argbuf[26:0];
            app_cmd  <= CMD_READ;
            app_en   <= 1'b1;
            state    <= S_RD_CMD;
          end
        end

        //---------------------------------------------------------------
        // Command and write-data channels handshake independently.
        S_WR: begin
          if (app_en && app_rdy) begin
            app_en   <= 1'b0;
            cmd_done <= 1'b1;
          end
          if (app_wdf_wren && app_wdf_rdy) begin
            app_wdf_wren <= 1'b0;
            app_wdf_end  <= 1'b0;
            wdf_done     <= 1'b1;
          end
          if ((cmd_done || (app_en && app_rdy)) &&
              (wdf_done || (app_wdf_wren && app_wdf_rdy))) begin
            resp    <= {8'h5A, 120'd0};    // ACK
            respcnt <= 5'd1;
            state   <= S_RESP;
          end
        end

        //---------------------------------------------------------------
        S_RD_CMD: begin
          if (app_rdy) begin
            app_en <= 1'b0;
            state  <= S_RD_WAIT;
          end
        end

        S_RD_WAIT: begin
          if (app_rd_data_valid) begin
            resp    <= app_rd_data;
            respcnt <= 5'd16;
            state   <= S_RESP;
          end
        end

        //---------------------------------------------------------------
        S_RESP: begin
          if (respcnt == 5'd0) begin
            state <= S_OPCODE;
          end else if (!tx_busy && !tx_send) begin
            tx_data <= resp[127:120];      // MSB first
            tx_send <= 1'b1;
            resp    <= {resp[119:0], 8'h00};
            respcnt <= respcnt - 1'b1;
          end
        end

        default: state <= S_OPCODE;

      endcase
    end
  end

endmodule
