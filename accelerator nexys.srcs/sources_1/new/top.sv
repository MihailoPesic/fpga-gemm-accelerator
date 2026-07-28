`timescale 1ns / 1ps
//=============================================================================
// top.sv  --  Stage 2: DDR2 calibration test
//
// Does nothing but bring up the memory controller. Every app_* input is tied
// off, so MIG initialises the DRAM, runs write-levelling and read training,
// then sits idle.
//
//   LED[15] = mmcm_locked          MMCM produced its clocks
//   LED[0]  = init_calib_complete  DDR2 calibrated and ready for commands
//
// LED[15] dark             -> clocking problem, DDR2 never got a chance
// LED[15] lit, LED[0] dark -> clocks fine, DDR2 calibration itself failed
// both lit                 -> stage 2 passed
//=============================================================================

module top (
  input  logic        CLK100MHZ,
  input  logic        CPU_RESETN,     // active low push button
  output logic [15:0] LED,

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
  // MIG outputs -- declared, unused at this stage
  //---------------------------------------------------------------------------
  logic         init_calib_complete;
  logic         ui_clk;              // 50 MHz, will clock the whole design later
  logic         ui_clk_sync_rst;
  logic         app_rdy;
  logic         app_wdf_rdy;
  logic [127:0] app_rd_data;
  logic         app_rd_data_end;
  logic         app_rd_data_valid;
  logic         app_sr_active;
  logic         app_ref_ack;
  logic         app_zq_ack;

  //---------------------------------------------------------------------------
  // Memory controller. Every app_* input is tied off -- an undriven input can
  // latch X and hang the controller's state machine.
  //---------------------------------------------------------------------------
  mig_7series_0 u_mig (
    // DDR2 pins straight through
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

    // Command path -- idle
    .app_addr            (27'd0),
    .app_cmd             (3'd0),
    .app_en              (1'b0),
    .app_rdy             (app_rdy),

    // Write data path -- idle
    .app_wdf_data        (128'd0),
    .app_wdf_mask        (16'd0),
    .app_wdf_wren        (1'b0),
    .app_wdf_end         (1'b0),
    .app_wdf_rdy         (app_wdf_rdy),

    // Read data path -- unused
    .app_rd_data         (app_rd_data),
    .app_rd_data_end     (app_rd_data_end),
    .app_rd_data_valid   (app_rd_data_valid),

    // Self-refresh / refresh / ZQ-calibrate are handled automatically
    .app_sr_req          (1'b0),
    .app_ref_req         (1'b0),
    .app_zq_req          (1'b0),
    .app_sr_active       (app_sr_active),
    .app_ref_ack         (app_ref_ack),
    .app_zq_ack          (app_zq_ack),

    // User interface clock, 200 MHz / 4 = 50 MHz
    .ui_clk              (ui_clk),
    .ui_clk_sync_rst     (ui_clk_sync_rst),

    // Both "No Buffer": these arrive from the MMCM, not from pins
    .sys_clk_i           (clk100),
    .clk_ref_i           (clk200),
    .sys_rst             (sys_rst_n)
  );

  //---------------------------------------------------------------------------
  // Status
  //---------------------------------------------------------------------------
  assign LED[0]    = init_calib_complete;
  assign LED[15]   = mmcm_locked;
  assign LED[14:1] = '0;

endmodule
