`timescale 1ns/1ps
// Buffered reads and one write burst may be active concurrently.
// See docs/axi-burst.md. READ_SLOTS=1 retains the serial implementation.
// Reset must be coordinated with the AXI interconnect and memory controller.
module gemm_axi_burst #(
    parameter integer TAG_W = 16,
    parameter integer READ_SLOTS = 1
) (
    input wire clk, rst,
    // READ_SLOTS=4: stop new reads until coordinated reset. An AR already
    // offered remains an obligation; never-offered commands complete with 8.
    // This input does not alter the serial read branch or the write engine.
    input wire rd_cancel,
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
    output wire axi_quiescent, local_idle, progress,

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
    typedef enum logic [2:0] {Q_FREE, Q_PENDING, Q_AR, Q_RECEIVE,
                             Q_DRAIN, Q_VALID, Q_DONE} slot_state_t;
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

    assign wr_cmd_ready = write_state == W_IDLE && !fatal && !rst;
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
    wire read_axi_active, read_local_idle, read_protocol;
    wire write_response_due = write_state == W_ACTIVE && address_accepted && write_data_complete;
    wire expected_rlast = read_count + 5'd1 == read_beats;
    wire write_protocol = b_fire && (!write_response_due || m_axi_bid != 0);
    wire response_error = (r_fire && m_axi_rresp != 0) || (b_fire && m_axi_bresp != 0);
    wire fault_now = read_protocol || write_protocol || response_error;
    wire [15:0] next_read_error = read_error != OK ? read_error :
        read_protocol ? PROTOCOL : (r_fire && m_axi_rresp != 0) ? MEM_RESP : OK;
    wire [15:0] next_write_error = write_error != OK ? write_error :
        write_protocol ? PROTOCOL : (b_fire && m_axi_bresp != 0) ? MEM_RESP : OK;
    assign wr_data_ready = write_state == W_COLLECT && !fatal && !fault_now && !rst;
    wire local_write_fire = wr_data_valid && wr_data_ready;
    assign axi_quiescent = !read_axi_active && write_state != W_ACTIVE;
    assign local_idle = read_local_idle && write_state == W_IDLE && !rst;
    assign progress = ar_fire || r_fire || aw_fire || w_fire || b_fire || local_write_fire ||
                      (rd_data_valid && rd_data_ready);

    generate if (READ_SLOTS == 1) begin : serial_read
    wire read_inflight = read_state == R_RECEIVE || read_state == R_DRAIN;
    assign rd_cmd_ready = read_state == R_IDLE && !fatal && !rst;
    assign rd_data_valid = read_state == R_DELIVER && !rst;
    assign rd_data = read_buffer[read_index];
    assign rd_data_index = read_index;
    assign rd_data_last = {1'b0, read_index} + 5'd1 == read_beats;
    assign rd_data_tag = read_tag;
    assign rd_done_valid = read_state == R_DONE && !rst;
    assign rd_done_tag = read_tag;
    assign m_axi_arvalid = read_state == R_AR && !rst;
    assign read_protocol = r_fire && (!read_inflight || m_axi_rid != 0 ||
                         (read_state != R_DRAIN && m_axi_rlast != expected_rlast));
    assign read_axi_active = read_state == R_AR || read_inflight;
    assign read_local_idle = read_state == R_IDLE;

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
    end else begin : queued_read
        slot_state_t slot_state [0:3];
        logic [31:0] slot_address [0:3];
        logic [4:0] slot_beats [0:3], slot_count [0:3];
        logic [TAG_W-1:0] slot_tag [0:3];
        logic [15:0] slot_status [0:3];
        logic [63:0] buffer_words [0:63];
        logic [1:0] allocate_slot, deliver_slot, issue_slot;
        logic [2:0] owned_count, issue_count;
        logic [3:0] deliver_index;
        logic ar_pending;
        logic [1:0] ar_slot;
        // Only AR handshakes enter this FIFO. Invalid local commands do not.
        logic [1:0] response_slot [0:3];
        logic [1:0] response_head, response_tail;
        logic [2:0] response_count;
        logic stream_poisoned;
        wire [1:0] receiving_slot = response_slot[response_head];
        wire response_due = response_count != 0;
        wire terminal_fire = r_fire && response_due && m_axi_rlast;
        wire command_fire = rd_cmd_valid && rd_cmd_ready;
        wire done_fire = rd_done_valid && rd_done_ready;
        logic read_cancelled;
        wire stop_reads = rd_cancel || read_cancelled;
        wire skip_issue = !ar_pending && issue_count != 0 &&
                          (slot_state[issue_slot] == Q_DONE ||
                           (slot_state[issue_slot] == Q_PENDING && (fatal || fault_now || stop_reads)));
        wire issue_retired = ar_fire || skip_issue;
        wire receive_last_expected = slot_count[receiving_slot] + 5'd1 == slot_beats[receiving_slot];
        wire [15:0] receive_status = slot_status[receiving_slot] != OK ? slot_status[receiving_slot] :
            (read_protocol || stream_poisoned) ? PROTOCOL : m_axi_rresp != 0 ? MEM_RESP : OK;

        assign rd_cmd_ready = owned_count < 4 && !fatal && !stop_reads && !rst;
        assign rd_data_valid = owned_count != 0 && slot_state[deliver_slot] == Q_VALID && !rst;
        assign rd_data = buffer_words[{deliver_slot, deliver_index}];
        assign rd_data_index = deliver_index;
        assign rd_data_last = {1'b0, deliver_index} + 5'd1 == slot_beats[deliver_slot];
        assign rd_data_tag = slot_tag[deliver_slot];
        assign rd_done_valid = owned_count != 0 && slot_state[deliver_slot] == Q_DONE && !rst;
        assign rd_done_tag = slot_tag[deliver_slot];
        assign rd_done_status = slot_status[deliver_slot];
        assign read_address = slot_address[ar_slot];
        assign read_beats = slot_beats[ar_slot];
        assign m_axi_arvalid = ar_pending && !rst;
        assign read_axi_active = ar_pending || response_count != 0;
        assign read_local_idle = owned_count == 0 && issue_count == 0 && !read_axi_active;
        // Use the FIFO occupancy before the edge: a simultaneous first AR/R
        // cannot make that premature R belong to the newly accepted command.
        assign read_protocol = r_fire && (!response_due || m_axi_rid != 0 ||
                                          m_axi_rlast != receive_last_expected);
        // The old serial buffer counter is unused in this elaboration.
        assign read_count = 0;

        integer slot;
        always_ff @(posedge clk) begin
            if (rst) begin
                allocate_slot <= 0; deliver_slot <= 0; issue_slot <= 0;
                owned_count <= 0; issue_count <= 0; deliver_index <= 0;
                ar_pending <= 0; ar_slot <= 0;
                response_head <= 0; response_tail <= 0; response_count <= 0;
                stream_poisoned <= 0;
                read_cancelled <= 0;
                for (slot=0; slot<4; slot=slot+1) begin
                    slot_state[slot] <= Q_FREE;
                    slot_address[slot] <= 0; slot_beats[slot] <= 0;
                    slot_count[slot] <= 0; slot_tag[slot] <= 0; slot_status[slot] <= OK;
                    response_slot[slot] <= 0;
                end
            end else begin
                if (rd_cancel) read_cancelled <= 1;
                case ({command_fire, done_fire})
                    2'b10: owned_count <= owned_count + 1'b1;
                    2'b01: owned_count <= owned_count - 1'b1;
                    default: begin end
                endcase
                case ({command_fire, issue_retired})
                    2'b10: issue_count <= issue_count + 1'b1;
                    2'b01: issue_count <= issue_count - 1'b1;
                    default: begin end
                endcase
                case ({ar_fire, terminal_fire})
                    2'b10: response_count <= response_count + 1'b1;
                    2'b01: response_count <= response_count - 1'b1;
                    default: begin end
                endcase
                if (command_fire) begin
                    slot_address[allocate_slot] <= rd_cmd_addr;
                    slot_beats[allocate_slot] <= rd_cmd_beats;
                    slot_tag[allocate_slot] <= rd_cmd_tag;
                    slot_count[allocate_slot] <= 0;
                    slot_status[allocate_slot] <= fault_now ? PROTOCOL :
                        command_legal(rd_cmd_addr, rd_cmd_beats) ? OK : BAD_DESC;
                    slot_state[allocate_slot] <= fault_now || !command_legal(rd_cmd_addr, rd_cmd_beats) ?
                        Q_DONE : Q_PENDING;
                    allocate_slot <= allocate_slot + 1'b1;
                end
                if (done_fire) begin
                    slot_state[deliver_slot] <= Q_FREE;
                    deliver_slot <= deliver_slot + 1'b1;
                    deliver_index <= 0;
                end else if (rd_data_valid && rd_data_ready) begin
                    if (rd_data_last) slot_state[deliver_slot] <= Q_DONE;
                    else deliver_index <= deliver_index + 1'b1;
                end
                if (skip_issue) begin
                    if (slot_state[issue_slot] == Q_PENDING) begin
                        slot_status[issue_slot] <= PROTOCOL;
                        slot_state[issue_slot] <= Q_DONE;
                    end
                    issue_slot <= issue_slot + 1'b1;
                end else if (!ar_pending && issue_count != 0 && slot_state[issue_slot] == Q_PENDING) begin
                    ar_pending <= 1;
                    ar_slot <= issue_slot;
                    slot_state[issue_slot] <= Q_AR;
                end
                if (ar_fire) begin
                    ar_pending <= 0;
                    slot_state[ar_slot] <= Q_RECEIVE;
                    response_slot[response_tail] <= ar_slot;
                    response_tail <= response_tail + 1'b1;
                    issue_slot <= issue_slot + 1'b1;
                end
                if (read_protocol) stream_poisoned <= 1;
                if (r_fire && response_due) begin
                    slot_status[receiving_slot] <= receive_status;
                    if (slot_count[receiving_slot] < 16)
                        slot_count[receiving_slot] <= slot_count[receiving_slot] + 1'b1;
                    if (receive_status == OK && slot_count[receiving_slot] < slot_beats[receiving_slot])
                        buffer_words[{receiving_slot, slot_count[receiving_slot][3:0]}] <= m_axi_rdata;
                    if (m_axi_rlast) begin
                        slot_state[receiving_slot] <= receive_status == OK ? Q_VALID : Q_DONE;
                        response_head <= response_head + 1'b1;
                    end else if (receive_status != OK) slot_state[receiving_slot] <= Q_DRAIN;
                end
            end
        end
`ifndef SYNTHESIS
        always @(posedge clk) if (!rst) begin
            if (owned_count > 4 || issue_count > owned_count || response_count > owned_count)
                $fatal(1, "read slot/FIFO occupancy invalid");
            if (ar_pending && slot_state[ar_slot] != Q_AR)
                $fatal(1, "held AR lost its read slot");
            if (response_due && slot_state[receiving_slot] != Q_RECEIVE && slot_state[receiving_slot] != Q_DRAIN)
                $fatal(1, "read response FIFO references an inactive slot");
            if (rd_data_valid && slot_status[deliver_slot] != OK)
                $fatal(1, "failed read published local data");
            if (command_fire && slot_state[allocate_slot] != Q_FREE)
                $fatal(1, "read slot reused before terminal consumption");
            if (ar_fire && response_count == 4)
                $fatal(1, "read response FIFO overflow");
            for (integer check_slot=0; check_slot<4; check_slot=check_slot+1)
                if (slot_count[check_slot] > 16) $fatal(1, "read slot buffer overflow");
        end
`endif
    end endgenerate

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
    initial if (READ_SLOTS != 1 && READ_SLOTS != 4)
        $fatal(1, "READ_SLOTS must be 1 or 4");
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
