`timescale 1ns/1ps
// One read and one write burst may be active concurrently. See docs/axi-burst.md.
// Reset must be coordinated with the AXI interconnect and memory controller.
module gemm_axi_burst #(
    parameter integer TAG_W = 16
) (
    input wire clk, rst,
    input wire rd_cmd_valid,
    output wire rd_cmd_ready,
    input wire [31:0] rd_cmd_addr,
    input wire [4:0] rd_cmd_beats,
    input wire [TAG_W-1:0] rd_cmd_tag,
    output wire rd_data_valid,
    input wire rd_data_ready,
    output wire [63:0] rd_data,
    output wire [3:0] rd_data_index,
    output wire rd_data_last,
    output wire [TAG_W-1:0] rd_data_tag,
    output wire rd_done_valid,
    input wire rd_done_ready,
    output logic [15:0] rd_done_status,
    output wire [TAG_W-1:0] rd_done_tag,
    input wire wr_cmd_valid,
    output wire wr_cmd_ready,
    input wire [31:0] wr_cmd_addr,
    input wire [4:0] wr_cmd_beats,
    input wire [TAG_W-1:0] wr_cmd_tag,
    input wire wr_data_valid,
    output wire wr_data_ready,
    input wire [63:0] wr_data,
    input wire [7:0] wr_data_strb,
    output wire wr_done_valid,
    input wire wr_done_ready,
    output logic [15:0] wr_done_status,
    output wire [TAG_W-1:0] wr_done_tag,
    output logic fatal,
    output logic [15:0] fatal_code,
    output wire axi_quiescent, progress,

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
    localparam logic [15:0] OK=0, BAD_DESC=3, MEM_RESP=7, PROTOCOL=8;
    typedef enum logic [2:0] {R_IDLE, R_AR, R_RECEIVE, R_DRAIN, R_DELIVER, R_DONE} read_state_t;
    typedef enum logic [1:0] {W_IDLE, W_COLLECT, W_ACTIVE, W_DONE} write_state_t;
    read_state_t read_state;
    write_state_t write_state;
    logic [31:0] read_address, write_address;
    logic [4:0] read_beats, write_beats, read_count, write_count;
    logic [3:0] read_index, write_index;
    logic [TAG_W-1:0] read_tag, write_tag;
    logic [15:0] read_error, write_error;
    logic address_accepted, write_data_complete;
    logic [63:0] read_buffer [0:15];
    logic [71:0] write_buffer [0:15];

    function automatic command_legal(input logic [31:0] address, input logic [4:0] beats);
        logic [12:0] page_end;
        logic [32:0] final_byte;
        begin
            page_end = {1'b0, address[11:0]} + {5'd0, beats, 3'd0};
            final_byte = {1'b0, address} + {25'd0, beats, 3'd0} - 33'd1;
            command_legal = beats >= 1 && beats <= 16 && address[2:0] == 0 &&
                            page_end <= 4096 && !final_byte[32];
        end
    endfunction

    assign rd_cmd_ready = read_state == R_IDLE && !fatal && !rst;
    assign wr_cmd_ready = write_state == W_IDLE && !fatal && !rst;
    assign rd_data_valid = read_state == R_DELIVER && !rst;
    assign rd_data = read_buffer[read_index];
    assign rd_data_index = read_index;
    assign rd_data_last = {1'b0, read_index} + 5'd1 == read_beats;
    assign rd_data_tag = read_tag;
    assign rd_done_valid = read_state == R_DONE && !rst;
    assign rd_done_tag = read_tag;
    assign wr_done_valid = write_state == W_DONE && !rst;
    assign wr_done_tag = write_tag;

    assign m_axi_arid = 0;
    assign m_axi_araddr = read_address;
    assign m_axi_arlen = {3'd0, read_beats} - 8'd1;
    assign m_axi_arsize = 3'd3;
    assign m_axi_arburst = 2'b01;
    assign m_axi_arlock = 0;
    assign m_axi_arcache = 0;
    assign m_axi_arprot = 0;
    assign m_axi_arqos = 0;
    assign m_axi_arregion = 0;
    // An asserted address is an obligation, including while READY is low.
    assign m_axi_arvalid = read_state == R_AR && !rst;
    assign m_axi_rready = !rst;
    assign m_axi_awid = 0;
    assign m_axi_awaddr = write_address;
    assign m_axi_awlen = {3'd0, write_beats} - 8'd1;
    assign m_axi_awsize = 3'd3;
    assign m_axi_awburst = 2'b01;
    assign m_axi_awlock = 0;
    assign m_axi_awcache = 0;
    assign m_axi_awprot = 0;
    assign m_axi_awqos = 0;
    assign m_axi_awregion = 0;
    assign m_axi_awvalid = write_state == W_ACTIVE && !address_accepted && !rst;
    assign m_axi_wdata = write_buffer[write_index][63:0];
    assign m_axi_wstrb = write_buffer[write_index][71:64];
    assign m_axi_wlast = {1'b0, write_index} + 5'd1 == write_beats;
    assign m_axi_wvalid = write_state == W_ACTIVE && !write_data_complete && !rst;
    // Always consume response channels: reserved read storage removes local
    // backpressure, and unsolicited beats are consumed as protocol faults.
    assign m_axi_bready = !rst;
    wire ar_fire = m_axi_arvalid && m_axi_arready;
    wire r_fire = m_axi_rvalid && m_axi_rready;
    wire aw_fire = m_axi_awvalid && m_axi_awready;
    wire w_fire = m_axi_wvalid && m_axi_wready;
    wire b_fire = m_axi_bvalid && m_axi_bready;
    // Responses follow handshakes from earlier edges. A premature response
    // cannot discharge an address/data obligation completed on this edge.
    wire read_inflight = read_state == R_RECEIVE || read_state == R_DRAIN;
    wire write_response_due = write_state == W_ACTIVE && address_accepted && write_data_complete;
    wire expected_rlast = read_count + 5'd1 == read_beats;
    wire read_protocol = r_fire && (!read_inflight || m_axi_rid != 0 ||
                         (read_state != R_DRAIN && m_axi_rlast != expected_rlast));
    wire write_protocol = b_fire && (!write_response_due || m_axi_bid != 0);
    wire response_error = (r_fire && m_axi_rresp != 0) || (b_fire && m_axi_bresp != 0);
    wire fault_now = read_protocol || write_protocol || response_error;
    wire [15:0] next_read_error = read_error != OK ? read_error :
        read_protocol ? PROTOCOL : (r_fire && m_axi_rresp != 0) ? MEM_RESP : OK;
    wire [15:0] next_write_error = write_error != OK ? write_error :
        write_protocol ? PROTOCOL : (b_fire && m_axi_bresp != 0) ? MEM_RESP : OK;
    assign wr_data_ready = write_state == W_COLLECT && !fatal && !fault_now && !rst;
    wire local_write_fire = wr_data_valid && wr_data_ready;
    assign axi_quiescent = read_state != R_AR && read_state != R_RECEIVE &&
                           read_state != R_DRAIN && write_state != W_ACTIVE;
    assign progress = ar_fire || r_fire || aw_fire || w_fire || b_fire || local_write_fire ||
                      (rd_data_valid && rd_data_ready);

    always_ff @(posedge clk) begin
        if (rst) begin
            fatal <= 1'b0;
            fatal_code <= OK;
        end else if (!fatal && fault_now) begin
            fatal <= 1'b1;
            // Simultaneous events prioritize a protocol inconsistency. Later
            // faults do not overwrite the first latched fatal code.
            fatal_code <= read_protocol || write_protocol ? PROTOCOL : MEM_RESP;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            read_state <= R_IDLE;
            read_address <= '0;
            read_beats <= '0;
            read_count <= '0;
            read_index <= '0;
            read_tag <= '0;
            read_error <= OK;
            rd_done_status <= OK;
        end else begin
            case (read_state)
                R_IDLE: if (rd_cmd_valid && rd_cmd_ready) begin
                    read_address <= rd_cmd_addr;
                    read_beats <= rd_cmd_beats;
                    read_tag <= rd_cmd_tag;
                    read_count <= '0;
                    read_index <= '0;
                    read_error <= OK;
                    if (fault_now) begin rd_done_status <= PROTOCOL; read_state <= R_DONE; end
                    else if (!command_legal(rd_cmd_addr, rd_cmd_beats)) begin
                        rd_done_status <= BAD_DESC;
                        read_state <= R_DONE;
                    end else read_state <= R_AR;
                end
                R_AR: if (ar_fire) read_state <= R_RECEIVE;
                R_DELIVER: if (rd_data_ready) begin
                    if (rd_data_last) begin rd_done_status <= OK; read_state <= R_DONE; end
                    else read_index <= read_index + 1'b1;
                end
                R_DONE: if (rd_done_ready) read_state <= R_IDLE;
                default: begin end
            endcase
            if (r_fire && read_inflight) begin
                read_error <= next_read_error;
                if (read_count < 16) read_count <= read_count + 1'b1;
                if (read_state != R_DRAIN && next_read_error == OK && read_count < read_beats)
                    read_buffer[read_count[3:0]] <= m_axi_rdata;
                if (m_axi_rlast) begin
                    if (next_read_error == OK) begin
                        read_index <= '0;
                        read_state <= R_DELIVER;
                    end else begin
                        rd_done_status <= next_read_error;
                        read_state <= R_DONE;
                    end
                end else if (next_read_error != OK) read_state <= R_DRAIN;
            end else if (r_fire && read_state == R_AR) begin
                // A premature response must not cancel AR or count toward its
                // response, even when AR is accepted on this same edge.
                read_error <= PROTOCOL;
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            write_state <= W_IDLE;
            write_address <= '0;
            write_beats <= '0;
            write_count <= '0;
            write_index <= '0;
            write_tag <= '0;
            write_error <= OK;
            address_accepted <= 1'b0;
            write_data_complete <= 1'b0;
            wr_done_status <= OK;
        end else begin
            case (write_state)
                W_IDLE: if (wr_cmd_valid && wr_cmd_ready) begin
                    write_address <= wr_cmd_addr;
                    write_beats <= wr_cmd_beats;
                    write_tag <= wr_cmd_tag;
                    write_count <= '0;
                    write_index <= '0;
                    write_error <= OK;
                    address_accepted <= 1'b0;
                    write_data_complete <= 1'b0;
                    if (fault_now) begin wr_done_status <= PROTOCOL; write_state <= W_DONE; end
                    else if (!command_legal(wr_cmd_addr, wr_cmd_beats)) begin
                        wr_done_status <= BAD_DESC;
                        write_state <= W_DONE;
                    end else write_state <= W_COLLECT;
                end
                W_COLLECT: if (fatal || fault_now) begin
                    // No AW/W has been asserted: cancel the local collection.
                    // The terminal rejection releases any pending local data.
                    wr_done_status <= PROTOCOL;
                    write_state <= W_DONE;
                end else if (local_write_fire) begin
                    write_buffer[write_count[3:0]] <= {wr_data_strb, wr_data};
                    write_count <= write_count + 1'b1;
                    if (write_count + 1'b1 == write_beats) write_state <= W_ACTIVE;
                end
                W_ACTIVE: begin
                    if (aw_fire) address_accepted <= 1'b1;
                    if (w_fire) begin
                        if (m_axi_wlast) write_data_complete <= 1'b1;
                        else write_index <= write_index + 1'b1;
                    end
                    if (b_fire) write_error <= next_write_error;
                    // Consume and report premature B, but retain the response
                    // obligation until a later, legally timed B is accepted.
                    if (b_fire && write_response_due) begin
                        wr_done_status <= next_write_error;
                        write_state <= W_DONE;
                    end
                end
                W_DONE: if (wr_done_ready) write_state <= W_IDLE;
                default: write_state <= W_IDLE;
            endcase
        end
    end

`ifndef SYNTHESIS
    // These check this master's obligations, including after a shared fault.
    logic hold_ar, hold_aw, hold_w, hold_data, hold_rd_done, hold_wr_done;
    logic [61:0] prior_ar, prior_aw;
    logic [72:0] prior_w;
    logic [TAG_W+68:0] prior_data;
    logic [TAG_W+15:0] prior_rd_done, prior_wr_done;
    wire [61:0] ar_payload = {m_axi_arid, m_axi_araddr, m_axi_arlen, m_axi_arsize,
        m_axi_arburst, m_axi_arlock, m_axi_arcache, m_axi_arprot, m_axi_arqos, m_axi_arregion};
    wire [61:0] aw_payload = {m_axi_awid, m_axi_awaddr, m_axi_awlen, m_axi_awsize,
        m_axi_awburst, m_axi_awlock, m_axi_awcache, m_axi_awprot, m_axi_awqos, m_axi_awregion};
    wire [72:0] w_payload = {m_axi_wdata, m_axi_wstrb, m_axi_wlast};
    wire [TAG_W+68:0] data_payload = {rd_data, rd_data_index, rd_data_last, rd_data_tag};
    always @(posedge clk) begin
        if (rst) begin
            hold_ar <= 0; hold_aw <= 0; hold_w <= 0;
            hold_data <= 0; hold_rd_done <= 0; hold_wr_done <= 0;
        end else begin
            if (hold_ar && (!m_axi_arvalid || ar_payload !== prior_ar)) $fatal(1, "stalled AR changed");
            if (hold_aw && (!m_axi_awvalid || aw_payload !== prior_aw)) $fatal(1, "stalled AW changed");
            if (hold_w && (!m_axi_wvalid || w_payload !== prior_w)) $fatal(1, "stalled W changed");
            if (hold_data && (!rd_data_valid || data_payload !== prior_data)) $fatal(1, "stalled read data changed");
            if (hold_rd_done && (!rd_done_valid || {rd_done_status, rd_done_tag} !== prior_rd_done))
                $fatal(1, "stalled read completion changed");
            if (hold_wr_done && (!wr_done_valid || {wr_done_status, wr_done_tag} !== prior_wr_done))
                $fatal(1, "stalled write completion changed");
            if ((m_axi_awvalid || m_axi_wvalid) && write_count != write_beats)
                $fatal(1, "write issued before complete prebuffer");
            if (read_count > 16 || write_count > 16) $fatal(1, "burst buffer overflow");
            hold_ar <= m_axi_arvalid && !m_axi_arready;
            hold_aw <= m_axi_awvalid && !m_axi_awready;
            hold_w <= m_axi_wvalid && !m_axi_wready;
            hold_data <= rd_data_valid && !rd_data_ready;
            hold_rd_done <= rd_done_valid && !rd_done_ready;
            hold_wr_done <= wr_done_valid && !wr_done_ready;
            prior_ar <= ar_payload; prior_aw <= aw_payload; prior_w <= w_payload;
            prior_data <= data_payload;
            prior_rd_done <= {rd_done_status, rd_done_tag};
            prior_wr_done <= {wr_done_status, wr_done_tag};
        end
    end
`endif
endmodule
