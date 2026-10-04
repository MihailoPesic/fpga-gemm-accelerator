`timescale 1ns/1ps
// Board diagnostic for the reusable AXI DDR2 platform. No GEMM job runs here.
module gemm_ddr_diag_top #(
    parameter integer BAUD = 115_200,
    parameter logic [31:0] BUILD_ID = 32'd0
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
    wire core_clk, core_rst, ddr_ready;
    wire start, busy, done, error;
    wire [31:0] seed, first_fail_addr;
    wire [15:0] error_code, engine_fatal_code;
    wire [63:0] expected, actual, cycles, read_beats, write_beats;
    wire engine_fatal, engine_progress, axi_quiescent;
    wire rd_cmd_valid;
    wire rd_cmd_ready;
    wire [31:0] rd_cmd_addr;
    wire [4:0] rd_cmd_beats;
    wire [15:0] rd_cmd_tag;
    wire rd_data_valid;
    wire rd_data_ready;
    wire [63:0] rd_data;
    wire [3:0] rd_data_index;
    wire rd_data_last;
    wire [15:0] rd_data_tag;
    wire rd_done_valid;
    wire rd_done_ready;
    wire [15:0] rd_done_status;
    wire [15:0] rd_done_tag;
    wire wr_cmd_valid;
    wire wr_cmd_ready;
    wire [31:0] wr_cmd_addr;
    wire [4:0] wr_cmd_beats;
    wire [15:0] wr_cmd_tag;
    wire wr_data_valid;
    wire wr_data_ready;
    wire [63:0] wr_data;
    wire [7:0] wr_data_strb;
    wire wr_done_valid;
    wire wr_done_ready;
    wire [15:0] wr_done_status;
    wire [15:0] wr_done_tag;
    wire axi_arid;
    wire [31:0] axi_araddr;
    wire [7:0] axi_arlen;
    wire [2:0] axi_arsize;
    wire [1:0] axi_arburst;
    wire axi_arlock;
    wire [3:0] axi_arcache;
    wire [2:0] axi_arprot;
    wire [3:0] axi_arqos;
    wire [3:0] axi_arregion;
    wire axi_arvalid;
    wire axi_arready;
    wire axi_rid;
    wire [63:0] axi_rdata;
    wire [1:0] axi_rresp;
    wire axi_rlast;
    wire axi_rvalid;
    wire axi_rready;
    wire axi_awid;
    wire [31:0] axi_awaddr;
    wire [7:0] axi_awlen;
    wire [2:0] axi_awsize;
    wire [1:0] axi_awburst;
    wire axi_awlock;
    wire [3:0] axi_awcache;
    wire [2:0] axi_awprot;
    wire [3:0] axi_awqos;
    wire [3:0] axi_awregion;
    wire axi_awvalid;
    wire axi_awready;
    wire [63:0] axi_wdata;
    wire [7:0] axi_wstrb;
    wire axi_wlast;
    wire axi_wvalid;
    wire axi_wready;
    wire axi_bid;
    wire [1:0] axi_bresp;
    wire axi_bvalid;
    wire axi_bready;

    axi_ddr_platform platform (
        .CLK100MHZ(CLK100MHZ), .CPU_RESETN(CPU_RESETN),
        .core_clk(core_clk), .core_rst(core_rst), .ddr_ready(ddr_ready),
        .ddr2_addr(ddr2_addr),
        .ddr2_ba(ddr2_ba),
        .ddr2_ras_n(ddr2_ras_n),
        .ddr2_cas_n(ddr2_cas_n),
        .ddr2_we_n(ddr2_we_n),
        .ddr2_ck_p(ddr2_ck_p),
        .ddr2_ck_n(ddr2_ck_n),
        .ddr2_cke(ddr2_cke),
        .ddr2_cs_n(ddr2_cs_n),
        .ddr2_dm(ddr2_dm),
        .ddr2_odt(ddr2_odt),
        .ddr2_dq(ddr2_dq),
        .ddr2_dqs_p(ddr2_dqs_p),
        .ddr2_dqs_n(ddr2_dqs_n),
        .s_axi_arid(axi_arid),
        .s_axi_araddr(axi_araddr),
        .s_axi_arlen(axi_arlen),
        .s_axi_arsize(axi_arsize),
        .s_axi_arburst(axi_arburst),
        .s_axi_arlock(axi_arlock),
        .s_axi_arcache(axi_arcache),
        .s_axi_arprot(axi_arprot),
        .s_axi_arqos(axi_arqos),
        .s_axi_arregion(axi_arregion),
        .s_axi_arvalid(axi_arvalid),
        .s_axi_arready(axi_arready),
        .s_axi_rid(axi_rid),
        .s_axi_rdata(axi_rdata),
        .s_axi_rresp(axi_rresp),
        .s_axi_rlast(axi_rlast),
        .s_axi_rvalid(axi_rvalid),
        .s_axi_rready(axi_rready),
        .s_axi_awid(axi_awid),
        .s_axi_awaddr(axi_awaddr),
        .s_axi_awlen(axi_awlen),
        .s_axi_awsize(axi_awsize),
        .s_axi_awburst(axi_awburst),
        .s_axi_awlock(axi_awlock),
        .s_axi_awcache(axi_awcache),
        .s_axi_awprot(axi_awprot),
        .s_axi_awqos(axi_awqos),
        .s_axi_awregion(axi_awregion),
        .s_axi_awvalid(axi_awvalid),
        .s_axi_awready(axi_awready),
        .s_axi_wdata(axi_wdata),
        .s_axi_wstrb(axi_wstrb),
        .s_axi_wlast(axi_wlast),
        .s_axi_wvalid(axi_wvalid),
        .s_axi_wready(axi_wready),
        .s_axi_bid(axi_bid),
        .s_axi_bresp(axi_bresp),
        .s_axi_bvalid(axi_bvalid),
        .s_axi_bready(axi_bready)
    );

    gemm_axi_burst transfer (
        .rd_cancel(1'b0),
        .clk(core_clk), .rst(core_rst),
        .fatal(engine_fatal), .fatal_code(engine_fatal_code),
        .progress(engine_progress), .axi_quiescent(axi_quiescent), .local_idle(),
        .rd_cmd_valid(rd_cmd_valid),
        .rd_cmd_ready(rd_cmd_ready),
        .rd_cmd_addr(rd_cmd_addr),
        .rd_cmd_beats(rd_cmd_beats),
        .rd_cmd_tag(rd_cmd_tag),
        .rd_data_valid(rd_data_valid),
        .rd_data_ready(rd_data_ready),
        .rd_data(rd_data),
        .rd_data_index(rd_data_index),
        .rd_data_last(rd_data_last),
        .rd_data_tag(rd_data_tag),
        .rd_done_valid(rd_done_valid),
        .rd_done_ready(rd_done_ready),
        .rd_done_status(rd_done_status),
        .rd_done_tag(rd_done_tag),
        .wr_cmd_valid(wr_cmd_valid),
        .wr_cmd_ready(wr_cmd_ready),
        .wr_cmd_addr(wr_cmd_addr),
        .wr_cmd_beats(wr_cmd_beats),
        .wr_cmd_tag(wr_cmd_tag),
        .wr_data_valid(wr_data_valid),
        .wr_data_ready(wr_data_ready),
        .wr_data(wr_data),
        .wr_data_strb(wr_data_strb),
        .wr_done_valid(wr_done_valid),
        .wr_done_ready(wr_done_ready),
        .wr_done_status(wr_done_status),
        .wr_done_tag(wr_done_tag),
        .m_axi_arid(axi_arid),
        .m_axi_araddr(axi_araddr),
        .m_axi_arlen(axi_arlen),
        .m_axi_arsize(axi_arsize),
        .m_axi_arburst(axi_arburst),
        .m_axi_arlock(axi_arlock),
        .m_axi_arcache(axi_arcache),
        .m_axi_arprot(axi_arprot),
        .m_axi_arqos(axi_arqos),
        .m_axi_arregion(axi_arregion),
        .m_axi_arvalid(axi_arvalid),
        .m_axi_arready(axi_arready),
        .m_axi_rid(axi_rid),
        .m_axi_rdata(axi_rdata),
        .m_axi_rresp(axi_rresp),
        .m_axi_rlast(axi_rlast),
        .m_axi_rvalid(axi_rvalid),
        .m_axi_rready(axi_rready),
        .m_axi_awid(axi_awid),
        .m_axi_awaddr(axi_awaddr),
        .m_axi_awlen(axi_awlen),
        .m_axi_awsize(axi_awsize),
        .m_axi_awburst(axi_awburst),
        .m_axi_awlock(axi_awlock),
        .m_axi_awcache(axi_awcache),
        .m_axi_awprot(axi_awprot),
        .m_axi_awqos(axi_awqos),
        .m_axi_awregion(axi_awregion),
        .m_axi_awvalid(axi_awvalid),
        .m_axi_awready(axi_awready),
        .m_axi_wdata(axi_wdata),
        .m_axi_wstrb(axi_wstrb),
        .m_axi_wlast(axi_wlast),
        .m_axi_wvalid(axi_wvalid),
        .m_axi_wready(axi_wready),
        .m_axi_bid(axi_bid),
        .m_axi_bresp(axi_bresp),
        .m_axi_bvalid(axi_bvalid),
        .m_axi_bready(axi_bready)
    );

    gemm_ddr_diag diagnostic (
        .clk(core_clk), .rst(core_rst), .start(start), .seed(seed), .ddr_ready(ddr_ready),
        .engine_fatal(engine_fatal), .engine_fatal_code(engine_fatal_code),
        .engine_progress(engine_progress), .axi_quiescent(axi_quiescent),
        .busy(busy),
        .done(done),
        .error(error),
        .error_code(error_code),
        .first_fail_addr(first_fail_addr),
        .expected(expected),
        .actual(actual),
        .cycles(cycles),
        .read_beats(read_beats),
        .write_beats(write_beats),
        .rd_cmd_valid(rd_cmd_valid),
        .rd_cmd_ready(rd_cmd_ready),
        .rd_cmd_addr(rd_cmd_addr),
        .rd_cmd_beats(rd_cmd_beats),
        .rd_cmd_tag(rd_cmd_tag),
        .rd_data_valid(rd_data_valid),
        .rd_data_ready(rd_data_ready),
        .rd_data(rd_data),
        .rd_data_index(rd_data_index),
        .rd_data_last(rd_data_last),
        .rd_data_tag(rd_data_tag),
        .rd_done_valid(rd_done_valid),
        .rd_done_ready(rd_done_ready),
        .rd_done_status(rd_done_status),
        .rd_done_tag(rd_done_tag),
        .wr_cmd_valid(wr_cmd_valid),
        .wr_cmd_ready(wr_cmd_ready),
        .wr_cmd_addr(wr_cmd_addr),
        .wr_cmd_beats(wr_cmd_beats),
        .wr_cmd_tag(wr_cmd_tag),
        .wr_data_valid(wr_data_valid),
        .wr_data_ready(wr_data_ready),
        .wr_data(wr_data),
        .wr_data_strb(wr_data_strb),
        .wr_done_valid(wr_done_valid),
        .wr_done_ready(wr_done_ready),
        .wr_done_status(wr_done_status),
        .wr_done_tag(wr_done_tag)
    );

    wire [7:0] rx_data, tx_data;
    wire rx_valid, rx_error, tx_valid, tx_ready, tx_busy;
    wire req_valid, req_ready, rsp_valid, rsp_ready;
    wire [7:0] req_opcode;
    wire [8:0] req_length, rsp_length;
    wire [2047:0] req_payload;
    wire [15:0] rsp_status;
    wire [1919:0] rsp_payload;
    uart_rx #(.CLK_HZ(100_000_000), .BAUD(BAUD)) receiver (
        .clk(core_clk), .rst(core_rst), .rx_pin(UART_TXD_IN),
        .data(rx_data), .valid(rx_valid), .frame_err(rx_error)
    );
    assign tx_ready = !core_rst && !tx_busy;
    uart_tx #(.CLK_HZ(100_000_000), .BAUD(BAUD)) transmitter (
        .clk(core_clk), .rst(core_rst), .data(tx_data), .send(tx_valid && tx_ready),
        .busy(tx_busy), .tx_pin(UART_RXD_OUT)
    );
    gemm_packet_transport transport (
        .clk(core_clk), .rst(core_rst), .rx_valid(rx_valid), .rx_error(rx_error), .rx_data(rx_data),
        .tx_valid(tx_valid), .tx_ready(tx_ready), .tx_data(tx_data),
        .req_valid(req_valid), .req_ready(req_ready), .req_opcode(req_opcode),
        .req_length(req_length), .req_payload(req_payload),
        .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_status(rsp_status),
        .rsp_length(rsp_length), .rsp_payload(rsp_payload)
    );
    gemm_ddr_diag_control #(.BUILD_ID(BUILD_ID)) control (
        .clk(core_clk), .rst(core_rst), .ddr_ready(ddr_ready), .start(start), .seed(seed),
        .req_valid(req_valid), .req_ready(req_ready), .req_opcode(req_opcode),
        .req_length(req_length), .req_payload(req_payload),
        .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_status(rsp_status),
        .rsp_length(rsp_length), .rsp_payload(rsp_payload),
        .busy(busy),
        .done(done),
        .error(error),
        .error_code(error_code),
        .first_fail_addr(first_fail_addr),
        .expected(expected),
        .actual(actual),
        .cycles(cycles),
        .read_beats(read_beats),
        .write_beats(write_beats)
    );
    assign LED = {error, done, busy, ddr_ready};
endmodule
