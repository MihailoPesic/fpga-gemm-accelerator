`timescale 1ns / 1ps
//=============================================================================
// top.sv  --  Stage 3: DDR2 calibration + UART echo
//
// Everything runs on ui_clk (50 MHz) from MIG. That is deliberate: the only
// clock-domain crossing left in the design is inside MIG, which Xilinx has
// already timed. Nothing for us to design, nothing for us to debug.
//
// The UART is reset from ui_clk_sync_rst only, NOT gated on
// init_calib_complete. So the link comes up even if DDR2 never calibrates,
// which keeps a debug channel alive in exactly the case you would need one.
//
//   LED[15] = mmcm_locked           MMCM produced its clocks
//   LED[14] = frame_err             stop bit was low -> baud is wrong
//   LED[1]  = heartbeat (~1.5 Hz)   design is alive on ui_clk
//   LED[0]  = init_calib_complete   DDR2 calibrated
//=============================================================================

module top (
  input  logic        CLK100MHZ,
  input  logic        CPU_RESETN,     // active low push button
  output logic [15:0] LED,

  // USB-UART. Names are from the HOST's point of view:
  //   UART_TXD_IN  = host transmits -> our receiver
  //   UART_RXD_OUT = our transmitter -> host receives
  input  logic        UART_TXD_IN,
  output logic        UART_RXD_OUT,

  // DDR2 -- names must match MIG's generated XDC exactly
  output logic [12:0] ddr2_addr,
  output logic [2:0]  ddr2_ba,
  output logic        ddr2_ras_n,
  output logic        ddr2_cas_n,
  output logic        ddr2_we_n,
  output logic [0:0]  ddr2_ck_p,
  output logic [0:0]  ddr2_ck_n,
  output logic [0:0]  ddr2_cke,
  output logic [0:0]  ddr2_cs_n,
  output logic [1:0]  ddr2_dm,
  output logic [0:0]  ddr2_odt,
  inout  wire  [15:0] ddr2_dq,        // bidirectional: driven by MIG's IOBUFs
  inout  wire  [1:0]  ddr2_dqs_p,
  inout  wire  [1:0]  ddr2_dqs_n
);

  localparam int UI_CLK_HZ = 50_000_000;   // 200 MHz memory clock / 4
  localparam int BAUD      = 921_600;      // divisor 54, +0.47% error

  //---------------------------------------------------------------------------
  // Clocking
  //---------------------------------------------------------------------------
  logic clk100;        // -> MIG sys_clk_i
  logic clk200;        // -> MIG clk_ref_i, IDELAYCTRL requires exactly this
  logic mmcm_locked;

  clk_wiz_0 u_clk (
    .clk_in1  (CLK100MHZ),
    .clk_out1 (clk100),
    .clk_out2 (clk200),
    .locked   (mmcm_locked)
  );

  // MIG's sys_rst is ACTIVE LOW. Holding it low until the MMCM locks matters:
  // releasing MIG against an unlocked clock fails calibration in a way that
  // looks like broken hardware.
  logic sys_rst_n;
  assign sys_rst_n = mmcm_locked & CPU_RESETN;

  //---------------------------------------------------------------------------
  // Memory controller. app_* still tied off -- no commands issued yet.
  //---------------------------------------------------------------------------
  logic         init_calib_complete;
  logic         ui_clk;
  logic         ui_clk_sync_rst;
  logic         app_rdy;
  logic         app_wdf_rdy;
  logic [127:0] app_rd_data;
  logic         app_rd_data_end;
  logic         app_rd_data_valid;
  logic         app_sr_active;
  logic         app_ref_ack;
  logic         app_zq_ack;

  mig_7series_0 u_mig (
    .ddr2_addr           (ddr2_addr),
    .ddr2_ba             (ddr2_ba),
    .ddr2_cas_n          (ddr2_cas_n),
    .ddr2_ck_n           (ddr2_ck_n),
    .ddr2_ck_p           (ddr2_ck_p),
    .ddr2_cke            (ddr2_cke),
    .ddr2_ras_n          (ddr2_ras_n),
    .ddr2_we_n           (ddr2_we_n),
    .ddr2_dq             (ddr2_dq),
    .ddr2_dqs_n          (ddr2_dqs_n),
    .ddr2_dqs_p          (ddr2_dqs_p),
    .ddr2_cs_n           (ddr2_cs_n),
    .ddr2_dm             (ddr2_dm),
    .ddr2_odt            (ddr2_odt),

    .init_calib_complete (init_calib_complete),

    .app_addr            (27'd0),
    .app_cmd             (3'd0),
    .app_en              (1'b0),
    .app_rdy             (app_rdy),

    .app_wdf_data        (128'd0),
    .app_wdf_mask        (16'd0),
    .app_wdf_wren        (1'b0),
    .app_wdf_end         (1'b0),
    .app_wdf_rdy         (app_wdf_rdy),

    .app_rd_data         (app_rd_data),
    .app_rd_data_end     (app_rd_data_end),
    .app_rd_data_valid   (app_rd_data_valid),

    .app_sr_req          (1'b0),
    .app_ref_req         (1'b0),
    .app_zq_req          (1'b0),
    .app_sr_active       (app_sr_active),
    .app_ref_ack         (app_ref_ack),
    .app_zq_ack          (app_zq_ack),

    .ui_clk              (ui_clk),
    .ui_clk_sync_rst     (ui_clk_sync_rst),

    .sys_clk_i           (clk100),
    .clk_ref_i           (clk200),
    .sys_rst             (sys_rst_n)
  );

  //---------------------------------------------------------------------------
  // UART, clocked by ui_clk
  //---------------------------------------------------------------------------
  logic [7:0] rx_data, tx_data;
  logic       rx_valid, frame_err;
  logic       tx_send, tx_busy;

  uart_rx #(.CLK_HZ(UI_CLK_HZ), .BAUD(BAUD)) u_rx (
    .clk       (ui_clk),
    .rst       (ui_clk_sync_rst),
    .rx_pin    (UART_TXD_IN),
    .data      (rx_data),
    .valid     (rx_valid),
    .frame_err (frame_err)
  );

  uart_tx #(.CLK_HZ(UI_CLK_HZ), .BAUD(BAUD)) u_tx (
    .clk       (ui_clk),
    .rst       (ui_clk_sync_rst),
    .data      (tx_data),
    .send      (tx_send),
    .busy      (tx_busy),
    .tx_pin    (UART_RXD_OUT)
  );

  //---------------------------------------------------------------------------
  // Echo, with one byte of skid.
  //
  // Receiver asserts valid at the centre of the stop bit; the transmitter needs
  // a full 10 bit-times to drain. Those rates are equal, so exactly one byte can
  // ever be waiting -- one holding register is provably enough, no FIFO needed.
  //---------------------------------------------------------------------------
  logic [7:0] hold;
  logic       pending;

  always_ff @(posedge ui_clk) begin
    tx_send <= 1'b0;                       // default: one-cycle pulse

    if (ui_clk_sync_rst) begin
      pending <= 1'b0;
    end else begin
      if (rx_valid) hold <= rx_data;

      if (pending && !tx_busy && !tx_send) begin
        tx_data <= hold;
        tx_send <= 1'b1;
        pending <= rx_valid;               // byte arriving this cycle stays queued
      end else if (rx_valid) begin
        pending <= 1'b1;
      end
    end
  end

  //---------------------------------------------------------------------------
  // Status
  //---------------------------------------------------------------------------
  logic [24:0] heartbeat;
  always_ff @(posedge ui_clk) heartbeat <= heartbeat + 1'b1;

  assign LED[0]     = init_calib_complete;
  assign LED[1]     = heartbeat[24];       // ~1.5 Hz at 50 MHz
  assign LED[13:2]  = '0;
  assign LED[14]    = frame_err;
  assign LED[15]    = mmcm_locked;

endmodule
