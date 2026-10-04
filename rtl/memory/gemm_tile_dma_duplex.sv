`timescale 1ns/1ps
// Independent operand-load and result-store contexts at the local burst boundary.
// The caller owns buffer lifetimes. This wrapper is not a macrotile scheduler.
module gemm_tile_dma_duplex #(
    parameter integer P=4, T=32, Q_W=$clog2(T), READ_SLOTS=1,
    parameter logic [32:0] DDR_BYTES=33'd134217728
) (
    input wire clk, rst,
    input wire load_req_valid,
    output wire load_req_ready,
    input wire load_req_bt, load_req_buf,
    input wire [31:0] load_req_base, load_req_stride,
    input wire [5:0] load_req_rows,
    input wire [8:0] load_req_row_bytes,
    output wire load_busy, load_done_valid,
    input wire load_done_ready,
    output wire [15:0] load_done_status,
    input wire store_req_valid,
    output wire store_req_ready,
    input wire store_req_buf,
    input wire [31:0] store_req_base, store_req_stride,
    input wire [5:0] store_req_rows,
    input wire [8:0] store_req_row_bytes,
    output wire store_busy, store_done_valid,
    input wire store_done_ready,
    output wire [15:0] store_done_status,
    output wire busy,
    input wire mem_fatal,
    input wire [15:0] mem_fatal_code,
    output wire fatal,
    output wire [15:0] fatal_code,
    output wire load_valid,
    input wire load_ready,
    output wire load_bt, load_buf,
    output wire [Q_W-1:0] load_q,
    output wire [4:0] load_word,
    output wire [63:0] load_data,
    output wire read_valid,
    input wire read_ready,
    output wire read_buf,
    output wire [Q_W-1:0] read_row,
    output wire [Q_W-2:0] read_pair,
    input wire response_valid,
    output wire response_ready,
    input wire [63:0] response_data,
    input wire [7:0] response_strb,
    input wire response_error,
    output wire rd_cmd_valid,
    input wire rd_cmd_ready,
    output wire [31:0] rd_cmd_addr,
    output wire [4:0] rd_cmd_beats,
    output wire [15:0] rd_cmd_tag,
    input wire rd_data_valid,
    output wire rd_data_ready,
    input wire [63:0] rd_data,
    input wire [3:0] rd_data_index,
    input wire rd_data_last,
    input wire [15:0] rd_data_tag,
    input wire rd_done_valid,
    output wire rd_done_ready,
    input wire [15:0] rd_done_status, rd_done_tag,
    output wire wr_cmd_valid,
    input wire wr_cmd_ready,
    output wire [31:0] wr_cmd_addr,
    output wire [4:0] wr_cmd_beats,
    output wire [15:0] wr_cmd_tag,
    output wire wr_data_valid,
    input wire wr_data_ready,
    output wire [63:0] wr_data,
    output wire [7:0] wr_data_strb,
    input wire wr_done_valid,
    output wire wr_done_ready,
    input wire [15:0] wr_done_status, wr_done_tag
);
    localparam logic [15:0] PROTOCOL=16'd8;
    logic first_fault_q;
    logic [15:0] first_code_q;
    wire reader_fatal, writer_fatal;
    wire [15:0] reader_fatal_code, writer_fatal_code;
    wire reader_req_ready, writer_req_ready;
    wire reader_cmd_valid, writer_cmd_valid;
    wire unused_reader_read_valid, unused_reader_response_ready;
    wire unused_reader_wr_cmd_valid, unused_reader_wr_data_valid, unused_reader_wr_done_ready;
    wire unused_writer_load_valid, unused_writer_rd_cmd_valid;
    wire unused_writer_rd_data_ready, unused_writer_rd_done_ready;

    function automatic [15:0] normalize_fault(input logic [15:0] code);
        case (code)
            16'd7, 16'd8, 16'd9, 16'd10: normalize_fault = code;
            default: normalize_fault = PROTOCOL;
        endcase
    endfunction

    // Child fatal outputs include their mem_fatal input. Feeding an aggregate
    // back combinationally would create a loop. A child fault reaches its
    // sibling's internal stop at the following wrapper observation edge.
    wire child_stop = mem_fatal || first_fault_q;
    wire [15:0] child_stop_code = first_fault_q ? first_code_q : normalize_fault(mem_fatal_code);
    assign fatal = first_fault_q || mem_fatal || reader_fatal || writer_fatal;
    assign fatal_code = first_fault_q ? first_code_q :
                        mem_fatal ? normalize_fault(mem_fatal_code) :
                        reader_fatal ? normalize_fault(reader_fatal_code) :
                        writer_fatal ? normalize_fault(writer_fatal_code) : 16'd0;
    assign busy = load_busy || store_busy;

    // Descriptor and local burst commands are cancelable before acceptance.
    // Guard both sides of each handshake immediately on an observed fault.
    // An operation/command accepted on the fault-detection edge remains owed.
    wire admit = !rst && !fatal;
    assign load_req_ready = reader_req_ready && admit;
    assign store_req_ready = writer_req_ready && admit;
    assign rd_cmd_valid = reader_cmd_valid && admit;
    assign wr_cmd_valid = writer_cmd_valid && admit;

    always_ff @(posedge clk) begin
        if (rst) begin
            first_fault_q <= 1'b0;
            first_code_q <= 16'd0;
        end else if (!first_fault_q && (mem_fatal || reader_fatal || writer_fatal)) begin
            first_fault_q <= 1'b1;
            // Same-edge priority is external memory, operand load, then store.
            first_code_q <= mem_fatal ? normalize_fault(mem_fatal_code) :
                            reader_fatal ? normalize_fault(reader_fatal_code) :
                                           normalize_fault(writer_fatal_code);
        end
    end

    // Accepted data, local-bank offers and operation results are not gated by
    // the sibling's fault. The existing adapters drain those obligations and
    // preserve held DONE payloads, even if their status was already zero.
    gemm_tile_dma #(.P(P), .T(T), .Q_W(Q_W), .READ_SLOTS(READ_SLOTS), .DDR_BYTES(DDR_BYTES)) reader (
        .clk(clk), .rst(rst),
        .req_valid(load_req_valid && admit), .req_ready(reader_req_ready),
        .req_write(1'b0), .req_bt(load_req_bt), .req_buf(load_req_buf),
        .req_base(load_req_base), .req_stride(load_req_stride),
        .req_rows(load_req_rows), .req_row_bytes(load_req_row_bytes),
        .busy(load_busy), .done_valid(load_done_valid), .done_ready(load_done_ready),
        .done_status(load_done_status), .mem_fatal(child_stop), .mem_fatal_code(child_stop_code),
        .fatal(reader_fatal), .fatal_code(reader_fatal_code),
        .load_valid(load_valid), .load_ready(load_ready), .load_bt(load_bt), .load_buf(load_buf),
        .load_q(load_q), .load_word(load_word), .load_data(load_data),
        .read_valid(unused_reader_read_valid), .read_ready(1'b0),
        .read_buf(), .read_row(), .read_pair(),
        .response_valid(1'b0), .response_ready(unused_reader_response_ready),
        .response_data(64'd0), .response_strb(8'd0), .response_error(1'b0),
        .rd_cmd_valid(reader_cmd_valid), .rd_cmd_ready(rd_cmd_ready && admit),
        .rd_cmd_addr(rd_cmd_addr), .rd_cmd_beats(rd_cmd_beats), .rd_cmd_tag(rd_cmd_tag),
        .rd_data_valid(rd_data_valid), .rd_data_ready(rd_data_ready), .rd_data(rd_data),
        .rd_data_index(rd_data_index), .rd_data_last(rd_data_last), .rd_data_tag(rd_data_tag),
        .rd_done_valid(rd_done_valid), .rd_done_ready(rd_done_ready),
        .rd_done_status(rd_done_status), .rd_done_tag(rd_done_tag),
        .wr_cmd_valid(unused_reader_wr_cmd_valid), .wr_cmd_ready(1'b0),
        .wr_cmd_addr(), .wr_cmd_beats(), .wr_cmd_tag(),
        .wr_data_valid(unused_reader_wr_data_valid), .wr_data_ready(1'b0), .wr_data(), .wr_data_strb(),
        .wr_done_valid(1'b0), .wr_done_ready(unused_reader_wr_done_ready),
        .wr_done_status(16'd0), .wr_done_tag(16'd0)
    );

    gemm_tile_dma #(.P(P), .T(T), .Q_W(Q_W), .READ_SLOTS(1), .DDR_BYTES(DDR_BYTES)) writer (
        .clk(clk), .rst(rst),
        .req_valid(store_req_valid && admit), .req_ready(writer_req_ready),
        .req_write(1'b1), .req_bt(1'b0), .req_buf(store_req_buf),
        .req_base(store_req_base), .req_stride(store_req_stride),
        .req_rows(store_req_rows), .req_row_bytes(store_req_row_bytes),
        .busy(store_busy), .done_valid(store_done_valid), .done_ready(store_done_ready),
        .done_status(store_done_status), .mem_fatal(child_stop), .mem_fatal_code(child_stop_code),
        .fatal(writer_fatal), .fatal_code(writer_fatal_code),
        .load_valid(unused_writer_load_valid), .load_ready(1'b0),
        .load_bt(), .load_buf(), .load_q(), .load_word(), .load_data(),
        .read_valid(read_valid), .read_ready(read_ready), .read_buf(read_buf),
        .read_row(read_row), .read_pair(read_pair),
        .response_valid(response_valid), .response_ready(response_ready),
        .response_data(response_data), .response_strb(response_strb), .response_error(response_error),
        .rd_cmd_valid(unused_writer_rd_cmd_valid), .rd_cmd_ready(1'b0),
        .rd_cmd_addr(), .rd_cmd_beats(), .rd_cmd_tag(),
        .rd_data_valid(1'b0), .rd_data_ready(unused_writer_rd_data_ready), .rd_data(64'd0),
        .rd_data_index(4'd0), .rd_data_last(1'b0), .rd_data_tag(16'd0),
        .rd_done_valid(1'b0), .rd_done_ready(unused_writer_rd_done_ready),
        .rd_done_status(16'd0), .rd_done_tag(16'd0),
        .wr_cmd_valid(writer_cmd_valid), .wr_cmd_ready(wr_cmd_ready && admit),
        .wr_cmd_addr(wr_cmd_addr), .wr_cmd_beats(wr_cmd_beats), .wr_cmd_tag(wr_cmd_tag),
        .wr_data_valid(wr_data_valid), .wr_data_ready(wr_data_ready),
        .wr_data(wr_data), .wr_data_strb(wr_data_strb),
        .wr_done_valid(wr_done_valid), .wr_done_ready(wr_done_ready),
        .wr_done_status(wr_done_status), .wr_done_tag(wr_done_tag)
    );

`ifndef SYNTHESIS
    logic previous_latched;
    logic [15:0] previous_code;
    always @(posedge clk) begin
        if (rst) begin
            previous_latched <= 1'b0;
            previous_code <= 16'd0;
        end else begin
            if (unused_reader_read_valid || unused_reader_response_ready ||
                unused_reader_wr_cmd_valid || unused_reader_wr_data_valid || unused_reader_wr_done_ready ||
                unused_writer_load_valid || unused_writer_rd_cmd_valid ||
                unused_writer_rd_data_ready || unused_writer_rd_done_ready)
                $fatal(1,"Duplex DMA used an inactive direction");
            if (previous_latched && (!first_fault_q || first_code_q !== previous_code ||
                                     !fatal || fatal_code !== previous_code))
                $fatal(1,"Duplex DMA first fault changed after capture");
            previous_latched <= first_fault_q;
            previous_code <= first_code_q;
        end
    end
`endif
endmodule
