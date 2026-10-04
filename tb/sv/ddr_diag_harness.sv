`timescale 1ns/1ps
// Portable integration harness. Fault injection exists only in simulation.
module ddr_diag_harness #(
    parameter integer WATCHDOG_LIMIT=64
) (
    input wire clk, rst, start, ddr_ready,
    input wire [31:0] seed,
    input wire [63:0] inject_r_xor,
    input wire [1:0] inject_rresp, inject_bresp,
    output wire busy, done, error,
    output wire [15:0] error_code,
    output wire [31:0] first_fail_addr,
    output wire [63:0] expected, actual, cycles, read_beats, write_beats,
    output wire [0:0] m_axi_arid,
    output wire [31:0] m_axi_araddr,
    output wire [7:0] m_axi_arlen,
    output wire [2:0] m_axi_arsize,
    output wire [1:0] m_axi_arburst,
    output wire m_axi_arlock,
    output wire [3:0] m_axi_arcache,
    output wire [2:0] m_axi_arprot,
    output wire [3:0] m_axi_arqos, m_axi_arregion,
    output wire m_axi_arvalid,
    input wire m_axi_arready,
    input wire [0:0] m_axi_rid,
    input wire [63:0] m_axi_rdata,
    input wire [1:0] m_axi_rresp,
    input wire m_axi_rlast, m_axi_rvalid,
    output wire m_axi_rready,
    output wire [0:0] m_axi_awid,
    output wire [31:0] m_axi_awaddr,
    output wire [7:0] m_axi_awlen,
    output wire [2:0] m_axi_awsize,
    output wire [1:0] m_axi_awburst,
    output wire m_axi_awlock,
    output wire [3:0] m_axi_awcache,
    output wire [2:0] m_axi_awprot,
    output wire [3:0] m_axi_awqos, m_axi_awregion,
    output wire m_axi_awvalid,
    input wire m_axi_awready,
    output wire [63:0] m_axi_wdata,
    output wire [7:0] m_axi_wstrb,
    output wire m_axi_wlast, m_axi_wvalid,
    input wire m_axi_wready,
    input wire [0:0] m_axi_bid,
    input wire [1:0] m_axi_bresp,
    input wire m_axi_bvalid,
    output wire m_axi_bready
);
    wire rd_cmd_valid, rd_cmd_ready, rd_data_valid, rd_data_ready;
    wire [31:0] rd_cmd_addr, wr_cmd_addr;
    wire [4:0] rd_cmd_beats, wr_cmd_beats;
    wire [15:0] rd_cmd_tag, rd_data_tag, rd_done_status, rd_done_tag;
    wire [63:0] rd_data, wr_data;
    wire [3:0] rd_data_index;
    wire rd_data_last, rd_done_valid, rd_done_ready;
    wire wr_cmd_valid, wr_cmd_ready, wr_data_valid, wr_data_ready;
    wire [7:0] wr_data_strb;
    wire [15:0] wr_cmd_tag, wr_done_status, wr_done_tag;
    wire wr_done_valid, wr_done_ready;
    wire engine_fatal, engine_progress, axi_quiescent;
    wire [15:0] engine_fatal_code;

    gemm_ddr_diag #(.WATCHDOG_LIMIT(WATCHDOG_LIMIT)) diag (.*);
    gemm_axi_burst burst (
        .rd_cancel(1'b0),
        .fatal(engine_fatal), .fatal_code(engine_fatal_code), .progress(engine_progress), .local_idle(),
        .m_axi_rdata(m_axi_rdata ^ inject_r_xor),
        .m_axi_rresp(inject_rresp != 0 ? inject_rresp : m_axi_rresp),
        .m_axi_bresp(inject_bresp != 0 ? inject_bresp : m_axi_bresp),
        .*
    );
endmodule
