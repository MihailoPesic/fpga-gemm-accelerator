`timescale 1ns/1ps
// DDR GEMM board wrapper. The shared platform owns clocks, coordinated
// reset, DDR calibration, and AXI width/clock conversion.
module gemm_ddr_top #(
    parameter integer P=4, T=32, BAUD=115_200, READ_SLOTS=1, ENABLE_OVERLAP=0,
    parameter logic [31:0] BUILD_ID=32'd0
) (
    input wire CLK100MHZ, CPU_RESETN, UART_TXD_IN,
    output wire UART_RXD_OUT,
    output wire [3:0] LED,
    output wire [12:0] ddr2_addr,
    output wire [2:0] ddr2_ba,
    output wire ddr2_ras_n, ddr2_cas_n, ddr2_we_n,
    output wire [0:0] ddr2_ck_p, ddr2_ck_n, ddr2_cke, ddr2_cs_n, ddr2_odt,
    output wire [1:0] ddr2_dm,
    inout wire [15:0] ddr2_dq,
    inout wire [1:0] ddr2_dqs_p, ddr2_dqs_n
);
    localparam integer CORE_HZ=100_000_000;
    wire core_clk, core_rst, ddr_ready;
    wire busy, host_busy, done, error;
    wire rx_valid, rx_error, tx_valid, tx_ready, tx_busy;
    wire [7:0] rx_data, tx_data;
    wire [0:0] m_axi_arid, m_axi_rid, m_axi_awid, m_axi_bid;
    wire [31:0] m_axi_araddr, m_axi_awaddr;
    wire [7:0] m_axi_arlen, m_axi_awlen;
    wire [2:0] m_axi_arsize, m_axi_awsize;
    wire [1:0] m_axi_arburst, m_axi_awburst;
    wire m_axi_arlock, m_axi_awlock;
    wire [3:0] m_axi_arcache, m_axi_awcache;
    wire [2:0] m_axi_arprot, m_axi_awprot;
    wire [3:0] m_axi_arqos, m_axi_awqos, m_axi_arregion, m_axi_awregion;
    wire m_axi_arvalid, m_axi_arready, m_axi_awvalid, m_axi_awready;
    wire [63:0] m_axi_rdata, m_axi_wdata;
    wire [1:0] m_axi_rresp, m_axi_bresp;
    wire m_axi_rlast, m_axi_rvalid, m_axi_rready;
    wire [7:0] m_axi_wstrb;
    wire m_axi_wlast, m_axi_wvalid, m_axi_wready, m_axi_bvalid, m_axi_bready;

    axi_ddr_platform platform (
        .CLK100MHZ(CLK100MHZ), .CPU_RESETN(CPU_RESETN),
        .core_clk(core_clk), .core_rst(core_rst), .ddr_ready(ddr_ready),
        .ddr2_addr(ddr2_addr), .ddr2_ba(ddr2_ba),
        .ddr2_ras_n(ddr2_ras_n), .ddr2_cas_n(ddr2_cas_n), .ddr2_we_n(ddr2_we_n),
        .ddr2_ck_p(ddr2_ck_p), .ddr2_ck_n(ddr2_ck_n),
        .ddr2_cke(ddr2_cke), .ddr2_cs_n(ddr2_cs_n), .ddr2_odt(ddr2_odt),
        .ddr2_dm(ddr2_dm), .ddr2_dq(ddr2_dq),
        .ddr2_dqs_p(ddr2_dqs_p), .ddr2_dqs_n(ddr2_dqs_n),
        .s_axi_arid(m_axi_arid), .s_axi_araddr(m_axi_araddr),
        .s_axi_arlen(m_axi_arlen), .s_axi_arsize(m_axi_arsize),
        .s_axi_arburst(m_axi_arburst), .s_axi_arlock(m_axi_arlock),
        .s_axi_arcache(m_axi_arcache), .s_axi_arprot(m_axi_arprot),
        .s_axi_arqos(m_axi_arqos), .s_axi_arregion(m_axi_arregion),
        .s_axi_arvalid(m_axi_arvalid), .s_axi_arready(m_axi_arready),
        .s_axi_rid(m_axi_rid), .s_axi_rdata(m_axi_rdata), .s_axi_rresp(m_axi_rresp),
        .s_axi_rlast(m_axi_rlast), .s_axi_rvalid(m_axi_rvalid), .s_axi_rready(m_axi_rready),
        .s_axi_awid(m_axi_awid), .s_axi_awaddr(m_axi_awaddr),
        .s_axi_awlen(m_axi_awlen), .s_axi_awsize(m_axi_awsize),
        .s_axi_awburst(m_axi_awburst), .s_axi_awlock(m_axi_awlock),
        .s_axi_awcache(m_axi_awcache), .s_axi_awprot(m_axi_awprot),
        .s_axi_awqos(m_axi_awqos), .s_axi_awregion(m_axi_awregion),
        .s_axi_awvalid(m_axi_awvalid), .s_axi_awready(m_axi_awready),
        .s_axi_wdata(m_axi_wdata), .s_axi_wstrb(m_axi_wstrb),
        .s_axi_wlast(m_axi_wlast), .s_axi_wvalid(m_axi_wvalid), .s_axi_wready(m_axi_wready),
        .s_axi_bid(m_axi_bid), .s_axi_bresp(m_axi_bresp),
        .s_axi_bvalid(m_axi_bvalid), .s_axi_bready(m_axi_bready)
    );

    uart_rx #(.CLK_HZ(CORE_HZ), .BAUD(BAUD)) receiver (
        .clk(core_clk), .rst(core_rst), .rx_pin(UART_TXD_IN),
        .data(rx_data), .valid(rx_valid), .frame_err(rx_error)
    );
    assign tx_ready = !core_rst && !tx_busy;
    uart_tx #(.CLK_HZ(CORE_HZ), .BAUD(BAUD)) transmitter (
        .clk(core_clk), .rst(core_rst), .data(tx_data), .send(tx_valid && tx_ready),
        .busy(tx_busy), .tx_pin(UART_RXD_OUT)
    );
    gemm_ddr_core #(.P(P), .T(T), .BUILD_ID(BUILD_ID), .CORE_HZ(CORE_HZ),
                    .READ_SLOTS(READ_SLOTS), .ENABLE_OVERLAP(ENABLE_OVERLAP)) core (
        .clk(core_clk), .rst(core_rst),
        .ready(), .reset_required(), .error_code(), .last_job_id(), .job_accepted(), .*
    );

    assign LED = {error, done, busy || host_busy, ddr_ready};
endmodule
