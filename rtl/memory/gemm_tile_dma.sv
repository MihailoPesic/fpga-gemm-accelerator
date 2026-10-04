`timescale 1ns/1ps
// Adapter between row planning, the tile engine's bank ports, and the
// external buffered AXI burst engine. The caller exclusively owns those ports
// and the selected buffer for the operation; compute/host access must not race DMA.
// Stores describe complete result rows: rows=M and row_bytes=4*N of that tile.
module gemm_tile_dma #(
    parameter integer P=4, T=32, Q_W=$clog2(T), READ_SLOTS=1,
    parameter logic [32:0] DDR_BYTES=33'd134217728
) (
    input wire clk, rst,
    input wire req_valid,
    output wire req_ready,
    input wire req_write, req_bt, req_buf,
    input wire [31:0] req_base, req_stride,
    input wire [5:0] req_rows,
    input wire [8:0] req_row_bytes,
    output wire busy, done_valid,
    input wire done_ready,
    output wire [15:0] done_status,
    input wire mem_fatal,
    input wire [15:0] mem_fatal_code,
    output wire fatal,
    output wire [15:0] fatal_code,
    output wire load_valid,
    input wire load_ready,
    output wire load_bt, load_buf,
    output wire [Q_W-1:0] load_q,
    output logic [4:0] load_word,
    output logic [63:0] load_data,
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
    output logic [63:0] wr_data,
    output logic [7:0] wr_data_strb,
    input wire wr_done_valid,
    output wire wr_done_ready,
    input wire [15:0] wr_done_status, wr_done_tag
);
    localparam logic [15:0] PROTOCOL=16'd8;
    typedef enum logic [3:0] {IDLE, CMD_READ, RECEIVE, CMD_WRITE, NEXT_PAIR,
                             OFFER_READ, WAIT_RESPONSE, SEND_WORD, WAIT_WRITE,
                             FINISH} state_t;
    state_t state;
    logic saved_bt, saved_buf;
    logic own_fatal;
    logic [15:0] own_fatal_code, completion_status;
    logic [31:0] address;
    logic [4:0] beats, row, first_word, read_count, delivered_count, write_count;
    logic [7:0] final_strb;
    logic load_pending;
    logic [4:0] serial_load_word;
    logic [63:0] serial_load_data;

    wire planner_req_ready, planner_burst_valid, planner_burst_write;
    wire [31:0] planner_addr;
    wire [4:0] planner_beats, planner_row, planner_word;
    wire [7:0] planner_strb;
    wire planner_complete_ready;
    wire planner_last;
    wire queue_ready, queue_complete_valid;
    wire [15:0] queue_complete_status, queue_fault;
    wire queue_load_valid, queue_load_bt, queue_load_buf;
    wire [Q_W-1:0] queue_load_q;
    wire [4:0] queue_load_word;
    wire [63:0] queue_load_data;
    wire queue_cmd_valid, queue_data_ready, queue_done_ready;
    wire [31:0] queue_cmd_addr;
    wire [4:0] queue_cmd_beats;
    wire [15:0] queue_cmd_tag;
    wire queued_read = READ_SLOTS == 4 && !planner_burst_write;
    wire bad_geometry = req_rows > 6'(T) || (req_write && req_row_bytes > 9'(4*T));
    // DONE is an immutable ready/valid result once offered. A later memory
    // fault therefore also travels on this independent, sticky fault channel;
    // the job controller must check it even when accepting a status-zero DONE.
    assign fatal = own_fatal || mem_fatal;
    assign fatal_code = own_fatal ? own_fatal_code :
                        mem_fatal ? (mem_fatal_code != 0 ? mem_fatal_code : PROTOCOL) : 16'd0;
    assign req_ready = planner_req_ready && state == IDLE && !fatal && !rst;
    wire request_fire = req_valid && req_ready;
    wire take_burst = planner_burst_valid && state == IDLE && !queued_read && !rst;

    gemm_dma_rows #(.DDR_BYTES(DDR_BYTES), .READ_SLOTS(READ_SLOTS)) rows (
        .clk(clk), .rst(rst),
        .req_valid(req_valid && state == IDLE && !fatal), .req_ready(planner_req_ready),
        .req_write(req_write), .req_base(req_base), .req_stride(req_stride),
        .req_rows(bad_geometry ? 6'd0 : req_rows), .req_row_bytes(req_row_bytes),
        .busy(busy), .burst_valid(planner_burst_valid),
        .burst_ready(queued_read ? queue_ready : state == IDLE && !rst),
        .burst_write(planner_burst_write), .burst_addr(planner_addr),
        .burst_beats(planner_beats), .burst_row(planner_row), .burst_word(planner_word),
        .burst_row_last(), .burst_last(planner_last), .burst_final_strb(planner_strb),
        .complete_valid(queued_read ? queue_complete_valid : state == FINISH && !rst),
        .complete_ready(planner_complete_ready),
        .complete_status(queued_read ? queue_complete_status : completion_status),
        .cancel(queued_read && (fatal || queue_fault != 0)),
        .cancel_status(fatal ? fatal_code : queue_fault), .done_valid(done_valid),
        .done_ready(done_ready), .done_status(done_status)
    );

    generate if (READ_SLOTS == 4) begin : four_reads
        gemm_tile_dma_read_queue #(.T(T), .Q_W(Q_W)) read_queue (
            .clk(clk), .rst(rst), .stop(fatal), .stop_status(fatal_code),
            .saved_bt(saved_bt), .saved_buf(saved_buf),
            .plan_valid(planner_burst_valid && !planner_burst_write), .plan_ready(queue_ready),
            .plan_addr(planner_addr), .plan_beats(planner_beats),
            .plan_row(planner_row), .plan_word(planner_word), .plan_last(planner_last),
            .complete_valid(queue_complete_valid), .complete_ready(planner_complete_ready),
            .complete_status(queue_complete_status), .fault_code(queue_fault),
            .load_valid(queue_load_valid), .load_ready(load_ready),
            .load_bt(queue_load_bt), .load_buf(queue_load_buf), .load_q(queue_load_q),
            .load_word(queue_load_word), .load_data(queue_load_data),
            .rd_cmd_valid(queue_cmd_valid), .rd_cmd_ready(rd_cmd_ready),
            .rd_cmd_addr(queue_cmd_addr), .rd_cmd_beats(queue_cmd_beats), .rd_cmd_tag(queue_cmd_tag),
            .rd_data_valid(rd_data_valid), .rd_data_ready(queue_data_ready),
            .rd_data(rd_data), .rd_data_index(rd_data_index), .rd_data_last(rd_data_last),
            .rd_data_tag(rd_data_tag), .rd_done_valid(rd_done_valid), .rd_done_ready(queue_done_ready),
            .rd_done_status(rd_done_status), .rd_done_tag(rd_done_tag)
        );
    end else begin : one_read
        assign queue_ready = 0;
        assign queue_complete_valid = 0;
        assign queue_complete_status = 0;
        assign queue_fault = 0;
        assign queue_load_valid = 0;
        assign queue_load_bt = 0;
        assign queue_load_buf = 0;
        assign queue_load_q = 0;
        assign queue_load_word = 0;
        assign queue_load_data = 0;
        assign queue_cmd_valid = 0;
        assign queue_cmd_addr = 0;
        assign queue_cmd_beats = 0;
        assign queue_cmd_tag = 0;
        assign queue_data_ready = 0;
        assign queue_done_ready = 0;
    end endgenerate

    // These are local commands, not AXI VALID signals. Before acceptance the
    // burst engine's fatal state explicitly cancels a command it cannot accept.
    assign rd_cmd_valid = READ_SLOTS == 4 ? queue_cmd_valid : state == CMD_READ && !mem_fatal && !rst;
    assign wr_cmd_valid = state == CMD_WRITE && !mem_fatal && !rst;
    assign rd_cmd_addr = READ_SLOTS == 4 ? queue_cmd_addr : address;
    assign wr_cmd_addr = address;
    assign rd_cmd_beats = READ_SLOTS == 4 ? queue_cmd_beats : beats;
    assign wr_cmd_beats = beats;
    assign rd_cmd_tag = READ_SLOTS == 4 ? queue_cmd_tag : 16'd0;
    assign wr_cmd_tag = 16'd0;

    // One held operand word isolates the bank port from error/drain decisions.
    // A load already offered must still handshake, even after a fatal event.
    assign load_valid = READ_SLOTS == 4 ? queue_load_valid : load_pending && !rst;
    assign load_bt = READ_SLOTS == 4 ? queue_load_bt : saved_bt;
    assign load_buf = READ_SLOTS == 4 ? queue_load_buf : saved_buf;
    assign load_q = READ_SLOTS == 4 ? queue_load_q : row[Q_W-1:0];
    assign load_word = READ_SLOTS == 4 ? queue_load_word : serial_load_word;
    assign load_data = READ_SLOTS == 4 ? queue_load_data : serial_load_data;
    // Replace a delivered word on the same edge, sustaining one bank write per cycle.
    assign rd_data_ready = READ_SLOTS == 4 ? queue_data_ready : state == RECEIVE && (!load_pending || load_ready) && !rst;
    assign rd_done_ready = READ_SLOTS == 4 ? queue_done_ready : state == RECEIVE && !load_pending && !rd_data_valid && !rst;
    wire rd_fire = READ_SLOTS == 1 && rd_data_valid && rd_data_ready;
    wire load_fire = READ_SLOTS == 1 && load_valid && load_ready;
    wire rd_end = READ_SLOTS == 1 && rd_done_valid && rd_done_ready;
    wire [5:0] operand_word = {1'b0,first_word} + {2'd0,rd_data_index};
    wire bad_read_word = rd_fire && (rd_data_tag != 0 || read_count >= beats ||
        rd_data_index != read_count[3:0] || rd_data_last != (read_count+5'd1 == beats) ||
        operand_word >= 32 || row >= T);

    // Only one result request or response is in flight. Its request cannot be
    // withdrawn on an engine cancellation; consume its response before ACKing
    // that cancellation. No duplicate whole-burst staging buffer is needed.
    wire [5:0] result_word = {1'b0,first_word} + {1'b0,write_count};
    wire [7:0] expected_strb = write_count+5'd1 == beats ? final_strb : 8'hff;
    assign read_valid = state == OFFER_READ && !rst;
    assign read_buf = saved_buf;
    assign read_row = row[Q_W-1:0];
    assign read_pair = result_word[Q_W-2:0];
    assign response_ready = state == WAIT_RESPONSE && !rst;
    assign wr_data_valid = state == SEND_WORD && !rst;
    wire response_fire = response_valid && response_ready;
    wire write_fire = wr_data_valid && wr_data_ready;
    wire write_active = state == NEXT_PAIR || state == OFFER_READ || state == WAIT_RESPONSE ||
                        state == SEND_WORD || state == WAIT_WRITE;
    assign wr_done_ready = write_active && state != OFFER_READ && state != WAIT_RESPONSE && !rst;
    wire wr_end = wr_done_valid && wr_done_ready;
    // An early OK completion is impossible for the real engine. It cannot
    // discharge the accepted write. Consume it, pad the remaining beats, and
    // wait for a genuine terminal completion. Only a nonzero engine-fatal
    // cancellation may legitimately terminate collection before all beats.
    wire early_completion = write_active && wr_done_valid && write_count != beats &&
                             !(wr_done_status != 0 && mem_fatal);
    wire wr_terminal = wr_end && !early_completion;
    wire bad_response = response_fire && (response_error || response_strb != expected_strb);
    logic [15:0] local_fault;
    always_comb begin
        local_fault = 0;
        if (queue_fault != 0) local_fault = queue_fault;
        else if (bad_read_word || bad_response || early_completion ||
            (rd_end && (rd_done_tag != 0 || (rd_done_status == 0 &&
                       (read_count != beats || delivered_count != beats)))) ||
            (wr_end && wr_done_tag != 0)) local_fault = PROTOCOL;
        else if (rd_end && rd_done_status != 0)
            local_fault = rd_done_status == 7 ? 16'd7 : PROTOCOL;
        else if (wr_end && wr_done_status != 0)
            local_fault = wr_done_status == 7 ? 16'd7 : PROTOCOL;
    end
    wire stop_work = fatal || local_fault != 0;
    wire [15:0] terminal_status = fatal ? fatal_code : local_fault;

    always_ff @(posedge clk) begin
        if (rst) begin
            state <= IDLE; saved_bt <= 0; saved_buf <= 0;
            own_fatal <= 0; own_fatal_code <= 0; completion_status <= 0;
            address <= 0; beats <= 0; row <= 0; first_word <= 0; final_strb <= 0;
            read_count <= 0; delivered_count <= 0; write_count <= 0;
            load_pending <= 0; serial_load_word <= 0; serial_load_data <= 0;
            wr_data <= 0; wr_data_strb <= 0;
        end else begin
            if (!own_fatal && (mem_fatal || local_fault != 0)) begin
                own_fatal <= 1;
                own_fatal_code <= mem_fatal ?
                    (mem_fatal_code != 0 ? mem_fatal_code : PROTOCOL) : local_fault;
            end
            if (request_fire) begin saved_bt <= req_bt; saved_buf <= req_buf; end
            if (load_fire) begin
                load_pending <= 0;
                delivered_count <= delivered_count + 1'b1;
            end
            if (rd_fire) begin
                if (read_count != 31) read_count <= read_count + 1'b1;
                if (!stop_work) begin
                    load_pending <= 1; serial_load_data <= rd_data;
                    serial_load_word <= operand_word[4:0];
                end
            end
            if (wr_terminal) begin
                completion_status <= terminal_status;
                state <= FINISH;
            end else case (state)
                IDLE: if (take_burst) begin
                    address <= planner_addr; beats <= planner_beats;
                    row <= planner_row; first_word <= planner_word; final_strb <= planner_strb;
                    read_count <= 0; delivered_count <= 0; write_count <= 0;
                    if (fatal) begin completion_status <= fatal_code; state <= FINISH; end
                    else state <= planner_burst_write ? CMD_WRITE : CMD_READ;
                end
                CMD_READ: if (fatal) begin completion_status <= fatal_code; state <= FINISH; end
                else if (rd_cmd_ready) state <= RECEIVE;
                RECEIVE: if (rd_end) begin completion_status <= terminal_status; state <= FINISH; end
                CMD_WRITE: if (fatal) begin completion_status <= fatal_code; state <= FINISH; end
                else if (wr_cmd_ready) state <= stop_work ? NEXT_PAIR : OFFER_READ;
                // Normal collection offers the next C pair immediately. Keep
                // this state only to fill an accepted burst after a fault.
                NEXT_PAIR: if (stop_work) begin
                    wr_data <= 0; wr_data_strb <= 0; state <= SEND_WORD;
                end else state <= OFFER_READ;
                OFFER_READ: if (read_ready) state <= WAIT_RESPONSE;
                WAIT_RESPONSE: if (response_fire) begin
                    wr_data <= stop_work ? 64'd0 : response_data;
                    wr_data_strb <= stop_work ? 8'd0 : response_strb;
                    state <= wr_done_valid && !early_completion ? WAIT_WRITE : SEND_WORD;
                end
                SEND_WORD: if (write_fire) begin
                    write_count <= write_count + 1'b1;
                    if (write_count+5'd1 == beats) state <= WAIT_WRITE;
                    else state <= stop_work ? NEXT_PAIR : OFFER_READ;
                end
                WAIT_WRITE: begin end
                FINISH: if (planner_complete_ready) state <= IDLE;
                default: state <= IDLE;
            endcase
        end
    end

`ifndef SYNTHESIS
    initial if ((P != 4 && P != 8) || (T != 8 && T != 32) || Q_W != $clog2(T))
        $fatal(1,"Unsupported tile DMA geometry");
    initial if (READ_SLOTS != 1 && READ_SLOTS != 4)
        $fatal(1,"READ_SLOTS must be 1 or 4");
    logic held_load, held_read, held_rd_cmd, held_wr_cmd, held_write, held_done;
    logic [Q_W+70:0] prior_load;
    logic [2*Q_W-1:0] prior_read;
    logic [52:0] prior_rd_cmd, prior_wr_cmd;
    logic [71:0] prior_write;
    logic [15:0] prior_done;
    always @(posedge clk) begin
        if (rst) begin
            held_load <= 0; held_read <= 0; held_rd_cmd <= 0;
            held_wr_cmd <= 0; held_write <= 0; held_done <= 0;
        end else begin
            if (held_load && (!load_valid || {load_bt,load_buf,load_q,load_word,load_data} !== prior_load))
                $fatal(1,"DMA load changed while stalled");
            if (held_read && (!read_valid || {read_buf,read_row,read_pair} !== prior_read))
                $fatal(1,"DMA result request changed while stalled");
            if (held_rd_cmd && !fatal && queue_fault == 0 &&
                (!rd_cmd_valid || {rd_cmd_addr,rd_cmd_beats,rd_cmd_tag} !== prior_rd_cmd))
                $fatal(1,"DMA read command changed without acceptance or fatal cancellation");
            if (held_wr_cmd && !fatal && (!wr_cmd_valid || {wr_cmd_addr,wr_cmd_beats,wr_cmd_tag} !== prior_wr_cmd))
                $fatal(1,"DMA write command changed without acceptance or fatal cancellation");
            if (held_write && (!wr_data_valid || {wr_data,wr_data_strb} !== prior_write))
                $fatal(1,"DMA write data changed without acceptance or terminal cancellation");
            if (held_done && (!done_valid || done_status !== prior_done))
                $fatal(1,"DMA operation result changed while stalled");
            if (load_valid && (load_q >= T || load_word >= 32))
                $fatal(1,"DMA operand address outside local banks");
            if (read_valid && (row >= T || result_word >= T/2))
                $fatal(1,"DMA result address outside local banks");
            if (rd_fire && ^{rd_data,rd_data_index,rd_data_last,rd_data_tag} === 1'bx)
                $fatal(1,"Unknown local read payload");
            if (response_fire && !response_error && ^{response_data,response_strb} === 1'bx)
                $fatal(1,"Unknown tile result payload");
            held_load <= load_valid && !load_ready;
            held_read <= read_valid && !read_ready;
            held_rd_cmd <= rd_cmd_valid && !rd_cmd_ready;
            held_wr_cmd <= wr_cmd_valid && !wr_cmd_ready;
            // The external engine explicitly cancels an unconsumed data offer
            // when its terminal completion is accepted. Early bogus OK does not.
            held_write <= wr_data_valid && !wr_data_ready && !wr_terminal;
            held_done <= done_valid && !done_ready;
            prior_load <= {load_bt,load_buf,load_q,load_word,load_data};
            prior_read <= {read_buf,read_row,read_pair};
            prior_rd_cmd <= {rd_cmd_addr,rd_cmd_beats,rd_cmd_tag};
            prior_wr_cmd <= {wr_cmd_addr,wr_cmd_beats,wr_cmd_tag};
            prior_write <= {wr_data,wr_data_strb}; prior_done <= done_status;
        end
    end
`endif
endmodule
