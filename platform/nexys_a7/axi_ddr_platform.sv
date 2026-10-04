`timescale 1ns/1ps
// Board clock/reset and DDR2 platform. The caller runs on core_clk and must
// hold its AXI interface in reset while core_rst is high. Start memory work
// only after ddr_ready; a later loss invalidates the active job.
module axi_ddr_platform (
    input wire CLK100MHZ, CPU_RESETN,
    output wire core_clk, core_rst, ddr_ready,
    output wire [12:0] ddr2_addr,
    output wire [2:0] ddr2_ba,
    output wire ddr2_ras_n, ddr2_cas_n, ddr2_we_n,
    output wire [0:0] ddr2_ck_p, ddr2_ck_n, ddr2_cke, ddr2_cs_n, ddr2_odt,
    output wire [1:0] ddr2_dm,
    inout wire [15:0] ddr2_dq,
    inout wire [1:0] ddr2_dqs_p, ddr2_dqs_n,

    input wire [0:0] s_axi_arid,
    input wire [31:0] s_axi_araddr,
    input wire [7:0] s_axi_arlen,
    input wire [2:0] s_axi_arsize,
    input wire [1:0] s_axi_arburst,
    input wire s_axi_arlock,
    input wire [3:0] s_axi_arcache,
    input wire [2:0] s_axi_arprot,
    input wire [3:0] s_axi_arqos, s_axi_arregion,
    input wire s_axi_arvalid,
    output wire s_axi_arready,
    output wire [0:0] s_axi_rid,
    output wire [63:0] s_axi_rdata,
    output wire [1:0] s_axi_rresp,
    output wire s_axi_rlast, s_axi_rvalid,
    input wire s_axi_rready,
    input wire [0:0] s_axi_awid,
    input wire [31:0] s_axi_awaddr,
    input wire [7:0] s_axi_awlen,
    input wire [2:0] s_axi_awsize,
    input wire [1:0] s_axi_awburst,
    input wire s_axi_awlock,
    input wire [3:0] s_axi_awcache,
    input wire [2:0] s_axi_awprot,
    input wire [3:0] s_axi_awqos, s_axi_awregion,
    input wire s_axi_awvalid,
    output wire s_axi_awready,
    input wire [63:0] s_axi_wdata,
    input wire [7:0] s_axi_wstrb,
    input wire s_axi_wlast, s_axi_wvalid,
    output wire s_axi_wready,
    output wire [0:0] s_axi_bid,
    output wire [1:0] s_axi_bresp,
    output wire s_axi_bvalid,
    input wire s_axi_bready
);
    wire calibrated_ui, calibrated_core;

    axi_ddr_bd_wrapper platform (
        .CLK100MHZ(CLK100MHZ), .CPU_RESETN(CPU_RESETN),
        .core_clk(core_clk), .core_rst(core_rst), .calibrated_ui(calibrated_ui),
        .DDR2_addr(ddr2_addr), .DDR2_ba(ddr2_ba),
        .DDR2_ras_n(ddr2_ras_n), .DDR2_cas_n(ddr2_cas_n), .DDR2_we_n(ddr2_we_n),
        .DDR2_ck_p(ddr2_ck_p), .DDR2_ck_n(ddr2_ck_n),
        .DDR2_cke(ddr2_cke), .DDR2_cs_n(ddr2_cs_n), .DDR2_odt(ddr2_odt),
        .DDR2_dm(ddr2_dm), .DDR2_dq(ddr2_dq),
        .DDR2_dqs_p(ddr2_dqs_p), .DDR2_dqs_n(ddr2_dqs_n),
        .S_AXI_arid(s_axi_arid), .S_AXI_araddr(s_axi_araddr),
        .S_AXI_arlen(s_axi_arlen), .S_AXI_arsize(s_axi_arsize),
        .S_AXI_arburst(s_axi_arburst), .S_AXI_arlock(s_axi_arlock),
        .S_AXI_arcache(s_axi_arcache), .S_AXI_arprot(s_axi_arprot),
        .S_AXI_arqos(s_axi_arqos),
        .S_AXI_arvalid(s_axi_arvalid), .S_AXI_arready(s_axi_arready),
        .S_AXI_rid(s_axi_rid), .S_AXI_rdata(s_axi_rdata),
        .S_AXI_rresp(s_axi_rresp), .S_AXI_rlast(s_axi_rlast),
        .S_AXI_rvalid(s_axi_rvalid), .S_AXI_rready(s_axi_rready),
        .S_AXI_awid(s_axi_awid), .S_AXI_awaddr(s_axi_awaddr),
        .S_AXI_awlen(s_axi_awlen), .S_AXI_awsize(s_axi_awsize),
        .S_AXI_awburst(s_axi_awburst), .S_AXI_awlock(s_axi_awlock),
        .S_AXI_awcache(s_axi_awcache), .S_AXI_awprot(s_axi_awprot),
        .S_AXI_awqos(s_axi_awqos),
        .S_AXI_awvalid(s_axi_awvalid), .S_AXI_awready(s_axi_awready),
        .S_AXI_wdata(s_axi_wdata), .S_AXI_wstrb(s_axi_wstrb),
        .S_AXI_wlast(s_axi_wlast), .S_AXI_wvalid(s_axi_wvalid),
        .S_AXI_wready(s_axi_wready), .S_AXI_bid(s_axi_bid),
        .S_AXI_bresp(s_axi_bresp), .S_AXI_bvalid(s_axi_bvalid),
        .S_AXI_bready(s_axi_bready)
    );

    // MIG calibration belongs to ui_clk. AXI payload CDC is owned entirely by
    // SmartConnect; only this single status bit crosses separately.
    xpm_cdc_single #(
        .DEST_SYNC_FF(3), .INIT_SYNC_FF(1), .SIM_ASSERT_CHK(1), .SRC_INPUT_REG(0)
    ) calibration_sync (
        .src_clk(1'b0), .src_in(calibrated_ui),
        .dest_clk(core_clk), .dest_out(calibrated_core)
    );
    assign ddr_ready = calibrated_core && !core_rst;

    // SmartConnect has no REGION port. This platform uses ordinary memory
    // with REGION=0; retain the public pins so the master boundary is explicit.
`ifndef SYNTHESIS
    always @(posedge core_clk) if (!core_rst) begin
        if (s_axi_arvalid && s_axi_arregion !== 4'd0)
            $fatal(1, "AXI DDR platform requires ARREGION=0");
        if (s_axi_awvalid && s_axi_awregion !== 4'd0)
            $fatal(1, "AXI DDR platform requires AWREGION=0");
    end
`endif
endmodule
