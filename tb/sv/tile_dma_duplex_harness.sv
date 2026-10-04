`timescale 1ns/1ps
// Production banks, compute and AXI engine; all gates/fault muxes are test-only.
module tile_dma_duplex_harness #(
    parameter integer P=4, T=8, READ_SLOTS=1,
    parameter integer Q_W=$clog2(T), DIM_W=$clog2(T+1)
) (
    input wire clk, rst,
    input wire load_req_valid, load_req_bt, load_req_buf,
    input wire [31:0] load_req_base, load_req_stride,
    input wire [5:0] load_req_rows,
    input wire [8:0] load_req_row_bytes,
    output wire load_req_ready, load_busy, load_done_valid,
    input wire load_done_ready,
    output wire [15:0] load_done_status,
    input wire store_req_valid, store_req_buf,
    input wire [31:0] store_req_base, store_req_stride,
    input wire [5:0] store_req_rows,
    input wire [8:0] store_req_row_bytes,
    output wire store_req_ready, store_busy, store_done_valid,
    input wire store_done_ready,
    output wire [15:0] store_done_status,
    output wire busy,
    output wire fatal,
    output wire [15:0] fatal_code,
    input wire start, input_buf, output_buf,
    input wire [DIM_W-1:0] m, n,
    input wire [8:0] k,
    output wire start_ready, compute_busy, compute_done, compute_cmd_error,
    output wire [63:0] job_cycles,
    input wire allow_load, allow_read, allow_response, allow_rd_cmd, allow_rd_done, allow_wr_cmd, allow_wr_data,
    input wire inject_response_error, inject_response_strb,
    input wire inject_rd_tag, inject_rd_index, inject_rd_last,
    input wire inject_wr_done, inject_spurious_b,
    input wire inject_mem_fatal,
    input wire [15:0] inject_mem_code,
    input wire [1:0] inject_rresp, inject_bresp,
    output wire axi_quiescent, local_idle,
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
    wire load_valid, load_ready, load_bt, load_buf, tile_load_ready;
    wire [Q_W-1:0] load_q;
    wire [4:0] load_word;
    wire [63:0] load_data;
    wire read_valid, read_ready, read_buf, tile_read_ready;
    wire [Q_W-1:0] read_row;
    wire [Q_W-2:0] read_pair;
    wire response_valid, response_ready, response_error;
    wire [63:0] response_data;
    wire [7:0] response_strb;
    wire tile_response_valid, tile_response_ready, tile_response_error;
    wire [63:0] tile_response_data;
    wire [7:0] tile_response_strb;
    wire rd_cmd_valid, rd_cmd_ready, burst_rd_cmd_ready, rd_data_valid, rd_data_ready;
    wire [31:0] rd_cmd_addr, wr_cmd_addr;
    wire [4:0] rd_cmd_beats, wr_cmd_beats;
    wire [15:0] rd_cmd_tag, rd_data_tag, rd_done_status, rd_done_tag;
    wire [63:0] rd_data, wr_data;
    wire [3:0] rd_data_index;
    wire rd_data_last, rd_done_valid, rd_done_ready, burst_rd_done_valid;
    wire burst_rd_done_ready = rd_done_ready && allow_rd_done;
    wire [15:0] burst_rd_data_tag;
    wire [3:0] burst_rd_data_index;
    wire burst_rd_data_last;
    wire wr_cmd_valid, wr_cmd_ready, burst_wr_cmd_ready, wr_data_valid, wr_data_ready, burst_wr_data_ready;
    wire [7:0] wr_data_strb;
    wire [15:0] wr_cmd_tag, wr_done_status, wr_done_tag;
    wire wr_done_valid, wr_done_ready;
    wire burst_wr_done_valid;
    wire engine_fatal, mem_fatal;
    wire [15:0] engine_fatal_code, mem_fatal_code;

    assign load_ready = tile_load_ready && allow_load;
    assign read_ready = tile_read_ready && allow_read;
    assign response_valid = tile_response_valid && allow_response;
    assign tile_response_ready = response_ready && allow_response;
    assign response_data = tile_response_data;
    assign response_strb = tile_response_strb ^ (inject_response_strb ? 8'h80 : 8'h00);
    assign response_error = tile_response_error || inject_response_error;
    assign rd_cmd_ready = burst_rd_cmd_ready && allow_rd_cmd;
    assign rd_done_valid = burst_rd_done_valid && allow_rd_done;
    assign wr_cmd_ready = burst_wr_cmd_ready && allow_wr_cmd;
    assign wr_data_ready = burst_wr_data_ready && allow_wr_data;
    assign rd_data_tag = burst_rd_data_tag ^ {15'd0, inject_rd_tag};
    assign rd_data_index = burst_rd_data_index ^ {3'd0, inject_rd_index};
    assign rd_data_last = burst_rd_data_last ^ inject_rd_last;
    assign mem_fatal = engine_fatal || inject_mem_fatal;
    assign mem_fatal_code = engine_fatal ? engine_fatal_code : inject_mem_code;
    assign wr_done_valid = burst_wr_done_valid || inject_wr_done;

    gemm_tile_dma_duplex #(.P(P), .T(T), .READ_SLOTS(READ_SLOTS)) dma (.*);
    gemm_tile_engine #(.P(P), .T(T), .CONCURRENT_PORTS(1)) tile (
        .load_valid(load_valid && allow_load), .load_ready(tile_load_ready),
        .read_valid(read_valid && allow_read), .read_ready(tile_read_ready),
        .response_valid(tile_response_valid),
        .response_ready(tile_response_ready),
        .response_error(tile_response_error), .response_data(tile_response_data),
        .response_strb(tile_response_strb), .busy(compute_busy), .done(compute_done),
        .cmd_error(compute_cmd_error), .result_valid(), .compute_cycles(), .microtiles(), .*
    );
    gemm_axi_burst #(.READ_SLOTS(READ_SLOTS)) burst (
        .rd_cancel(fatal),
        .rd_cmd_valid(rd_cmd_valid && allow_rd_cmd), .rd_cmd_ready(burst_rd_cmd_ready),
        .wr_cmd_valid(wr_cmd_valid && allow_wr_cmd), .wr_cmd_ready(burst_wr_cmd_ready),
        .wr_data_valid(wr_data_valid && allow_wr_data), .wr_data_ready(burst_wr_data_ready),
        .rd_data_tag(burst_rd_data_tag), .rd_data_index(burst_rd_data_index),
        .rd_data_last(burst_rd_data_last), .fatal(engine_fatal),
        .rd_done_valid(burst_rd_done_valid), .rd_done_ready(burst_rd_done_ready),
        .wr_done_valid(burst_wr_done_valid),
        .fatal_code(engine_fatal_code), .progress(), .local_idle(local_idle),
        .m_axi_rresp(inject_rresp != 0 ? inject_rresp : m_axi_rresp),
        .m_axi_bvalid(m_axi_bvalid || inject_spurious_b),
        .m_axi_bid(inject_spurious_b ? 1'b0 : m_axi_bid),
        .m_axi_bresp(inject_spurious_b ? 2'b00 : inject_bresp != 0 ? inject_bresp : m_axi_bresp), .*
    );
endmodule
