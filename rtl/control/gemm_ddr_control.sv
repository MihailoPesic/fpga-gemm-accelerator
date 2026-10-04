`timescale 1ns/1ps
// Packet backend and bounded host-memory owner. Framing, CRC and replay are
// handled by gemm_packet_transport. Reset must coordinate the whole AXI path.
module gemm_ddr_control #(
    parameter logic [31:0] ID=32'h314d474e, VERSION=32'h00000100,
    parameter logic [32:0] DDR_BYTES=33'd134217728
) (
    input wire clk, rst,
    input wire req_valid,
    output wire req_ready,
    input wire [7:0] req_opcode,
    input wire [8:0] req_length,
    input wire [2047:0] req_payload,
    output wire rsp_valid,
    input wire rsp_ready,
    output logic [15:0] rsp_status,
    output logic [8:0] rsp_length,
    output logic [1919:0] rsp_payload,
    input wire job_busy, job_ready, ddr_ready, reset_required,
    input wire mem_fatal,
    input wire [15:0] mem_fatal_code,
    input wire mem_progress, axi_quiescent,
    input wire [31:0] watchdog_limit,
    output wire host_busy,
    output logic host_fatal,
    output logic [15:0] host_fatal_code,
    output wire reg_busy_guard,
    output wire reg_req_valid,
    input wire reg_req_ready,
    output wire [15:0] reg_req_addr,
    output wire reg_req_write,
    output wire [31:0] reg_req_wdata,
    output wire [3:0] reg_req_wstrb,
    input wire reg_rsp_valid,
    output wire reg_rsp_ready,
    input wire [31:0] reg_rsp_rdata,
    input wire [15:0] reg_rsp_status,
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
    localparam logic [15:0] OK=0, BAD_CMD=1, BAD_ADDR=2, BUSY=4,
        NOT_READY=5, PROTOCOL=8, WATCHDOG=9, CALIB_LOST=10;
    typedef enum logic [2:0] {IDLE, DECODE, REG_REQUEST, REG_RESPONSE, MEMORY, REPLY} packet_state_t;
    typedef enum logic [2:0] {M_IDLE, READ_COMMAND, READ_DATA, WRITE_COMMAND,
                              WRITE_DATA, WRITE_DONE, FINISH, DRAIN} memory_state_t;
    packet_state_t packet_state;
    memory_state_t memory_state;
    logic [7:0] opcode;
    logic [8:0] packet_length;
    logic [2047:0] packet_payload;
    logic accepted_busy;
    // Retained separately from packet_payload: after a fatal response, a new
    // diagnostic packet may arrive while this accepted write still drains.
    logic [1919:0] write_payload;
    logic [31:0] memory_address, timeout_threshold, idle_cycles;
    logic [5:0] words_left, word_offset;
    logic [4:0] burst_beats, beat_count;
    logic memory_complete;

    wire [15:0] requested_bytes = packet_payload[47:32];
    wire [32:0] requested_end = {1'b0,packet_payload[31:0]} + {17'd0,requested_bytes};
    wire bad_length = requested_bytes < 8 || requested_bytes > 240 || requested_bytes[2:0] != 0;
    wire bad_address = packet_payload[2:0] != 0 || requested_end > DDR_BYTES;
    function automatic [4:0] plan_burst(input logic [31:0] address,
                                        input logic [5:0] remaining_words);
        logic [4:0] word_limit, page_limit;
        begin
            word_limit = remaining_words > 16 ? 5'd16 : remaining_words[4:0];
            // Bursts never exceed 128 bytes. Only the last 128-byte block
            // of a page can therefore shorten a burst at the 4 KiB boundary.
            page_limit = address[11:7] == 5'h1f ?
                         5'd16 - {1'b0,address[6:3]} : 5'd16;
            plan_burst = word_limit < page_limit ? word_limit : page_limit;
        end
    endfunction
    wire [31:0] next_memory_address = memory_address + {24'd0,burst_beats,3'd0};
    wire [5:0] next_words_left = words_left - {1'b0,burst_beats};
    wire [6:0] payload_word = {1'b0,word_offset} + {2'd0,beat_count};

    assign req_ready = packet_state == IDLE && !rst;
    assign rsp_valid = packet_state == REPLY && !rst;
    assign host_busy = memory_state != M_IDLE;
    assign reg_req_valid = packet_state == REG_REQUEST && !rst;
    assign reg_req_addr = packet_payload[15:0];
    assign reg_req_write = opcode == 8'h03;
    assign reg_req_wdata = packet_payload[47:16];
    assign reg_req_wstrb = 4'hf;
    assign reg_rsp_ready = packet_state == REG_RESPONSE && !rst;
    // OR this guard into the register block's job_busy input. It preserves the
    // packet-admission epoch, including a job finishing before local decode.
    assign reg_busy_guard = (packet_state == REG_REQUEST || packet_state == REG_RESPONSE) &&
                            reg_req_write && (accepted_busy || host_busy);

    // These are local commands, not AXI VALID signals. An unaccepted local
    // command may be canceled on fatal; accepted commands retain ownership.
    assign rd_cmd_valid = memory_state == READ_COMMAND && !host_fatal && !mem_fatal && ddr_ready && !rst;
    assign wr_cmd_valid = memory_state == WRITE_COMMAND && !host_fatal && !mem_fatal && ddr_ready && !rst;
    assign rd_cmd_addr = memory_address;
    assign wr_cmd_addr = memory_address;
    // Prepare geometry at operation admission and terminal retirement. The
    // burst engine validates a registered offer, not this planner's arithmetic.
    assign rd_cmd_beats = burst_beats;
    assign wr_cmd_beats = burst_beats;
    assign rd_cmd_tag = 0;
    assign wr_cmd_tag = 0;
    assign rd_data_ready = memory_state == READ_DATA && !rst;
    assign rd_done_ready = memory_state == READ_DATA && !rst;
    // Host faults do not cancel an accepted W_COLLECT. Captured data continue
    // until its terminal response explicitly releases that local obligation.
    assign wr_data_valid = memory_state == WRITE_DATA && !rst;
    assign wr_data = payload_word < 30 ? write_payload[{payload_word,6'b0} +: 64] : 64'd0;
    assign wr_data_strb = 8'hff;
    assign wr_done_ready = (memory_state == WRITE_DATA || memory_state == WRITE_DONE) && !rst;

    wire read_command_fire = rd_cmd_valid && rd_cmd_ready;
    wire write_command_fire = wr_cmd_valid && wr_cmd_ready;
    wire read_fire = rd_data_valid && rd_data_ready;
    wire write_fire = wr_data_valid && wr_data_ready;
    wire read_done_fire = rd_done_valid && rd_done_ready;
    wire write_done_fire = wr_done_valid && wr_done_ready;
    wire [5:0] read_count_after = {1'b0,beat_count} + {5'd0,read_fire};
    wire bad_read_data = read_fire && (beat_count >= burst_beats || payload_word >= 30 ||
        rd_data_tag != 0 || rd_data_index != beat_count[3:0] ||
        rd_data_last != (beat_count + 5'd1 == burst_beats));
    wire early_read_done = read_done_fire && rd_done_status == OK &&
                           read_count_after != {1'b0,burst_beats};
    wire early_write_done = write_done_fire && memory_state == WRITE_DATA &&
                            !(wr_done_status != OK && mem_fatal);
    wire local_protocol = bad_read_data || early_read_done || early_write_done ||
        (read_done_fire && rd_done_tag != 0) || (write_done_fire && wr_done_tag != 0);
    wire terminal_error = (read_done_fire && rd_done_status != OK) ||
                          (write_done_fire && wr_done_status != OK);
    wire [15:0] terminal_code = read_done_fire ? rd_done_status : wr_done_status;
    wire progress = mem_progress || read_command_fire || write_command_fire ||
                    read_fire || write_fire || read_done_fire || write_done_fire;
    // The threshold is fixed for an operation. Starting from zero, the idle
    // counter cannot skip its first equality: progress resets it, otherwise it
    // advances by one. That first hit latches a fatal error before any larger
    // count matters. Keep subtraction out of the active fault/ownership path.
    wire timeout_now = host_busy && memory_state != DRAIN &&
                       !(memory_state == FINISH && axi_quiescent) && !progress &&
                       idle_cycles == timeout_threshold;
    logic fault_now;
    logic [15:0] fault_code;
    function automatic [15:0] fatal_status(input logic [15:0] code);
        fatal_status = code >= 7 && code <= 10 ? code : PROTOCOL;
    endfunction
    always_comb begin
        fault_now = 1'b0; fault_code = OK;
        // Completion is not published until the packet leaves MEMORY. Keep
        // calibration loss fatal through that final response-decision edge.
        if ((host_busy || packet_state == MEMORY) && !ddr_ready) begin fault_now=1'b1; fault_code=CALIB_LOST; end
        else if (mem_fatal) begin fault_now=1'b1; fault_code=fatal_status(mem_fatal_code); end
        else if (local_protocol) begin fault_now=1'b1; fault_code=PROTOCOL; end
        else if (terminal_error) begin fault_now=1'b1; fault_code=fatal_status(terminal_code); end
        else if (timeout_now) begin fault_now=1'b1; fault_code=WATCHDOG; end
    end
    wire poisoned = host_fatal || fault_now;
    wire valid_read_terminal = read_done_fire && !early_read_done;
    wire valid_write_terminal = write_done_fire && !early_write_done;
    wire burst_complete = valid_read_terminal || valid_write_terminal;

    always_ff @(posedge clk) begin
        if (rst) begin
            packet_state <= IDLE; memory_state <= M_IDLE;
            opcode <= 0; packet_length <= 0; packet_payload <= 0; accepted_busy <= 0;
            write_payload <= 0;
            memory_address <= 0; timeout_threshold <= 0; idle_cycles <= 0;
            words_left <= 0; word_offset <= 0; burst_beats <= 0; beat_count <= 0;
            memory_complete <= 0;
            host_fatal <= 0; host_fatal_code <= OK;
            rsp_status <= OK; rsp_length <= 0; rsp_payload <= 0;
        end else begin
            memory_complete <= 1'b0;
            if (!host_fatal && fault_now) begin
                host_fatal <= 1'b1;
                host_fatal_code <= fault_code;
            end
            if (!host_busy || progress) idle_cycles <= 0;
            else if (!host_fatal && idle_cycles != 32'hffffffff) idle_cycles <= idle_cycles + 1'b1;

            case (packet_state)
                IDLE: if (req_valid) begin
                    opcode <= req_opcode; packet_length <= req_length; packet_payload <= req_payload;
                    accepted_busy <= job_busy || host_busy;
                    packet_state <= DECODE;
                end
                DECODE: begin
                    rsp_status <= OK; rsp_length <= 0; rsp_payload <= 0;
                    packet_state <= REPLY;
                    case (opcode)
                        8'h01: if (packet_length != 4) rsp_status <= BAD_CMD;
                            else begin rsp_length <= 12; rsp_payload[95:0] <= {VERSION,ID,packet_payload[31:0]}; end
                        8'h02: if (packet_length != 2) rsp_status <= BAD_CMD;
                            else packet_state <= REG_REQUEST;
                        8'h03: if (packet_length != 6) rsp_status <= BAD_CMD;
                            // Block START during the registered fatal feedback
                            // interval; busy admission keeps its BUSY priority.
                            else if (packet_payload[15:0] == 16'h0010 && packet_payload[47:16] == 1 &&
                                     !accepted_busy && !job_busy && !host_busy &&
                                     (host_fatal || mem_fatal || reset_required)) rsp_status <= NOT_READY;
                            else packet_state <= REG_REQUEST;
                        8'h04, 8'h05: begin
                            if (bad_length || (opcode == 8'h04 && packet_length != 6) ||
                                (opcode == 8'h05 && {8'd0,packet_length} != {1'b0,requested_bytes}+17'd6))
                                rsp_status <= BAD_CMD;
                            else if (bad_address) rsp_status <= BAD_ADDR;
                            else if (accepted_busy || job_busy || host_busy) rsp_status <= BUSY;
                            // This branch has already excluded host_busy. All
                            // active local fault terms are then false, leaving
                            // only the registered fatal state or memory fault.
                            else if (!job_ready || !ddr_ready || reset_required ||
                                     host_fatal || mem_fatal || !axi_quiescent)
                                rsp_status <= NOT_READY;
                            else begin
                                memory_address <= packet_payload[31:0];
                                words_left <= requested_bytes[8:3]; word_offset <= 0; beat_count <= 0;
                                burst_beats <= plan_burst(packet_payload[31:0],requested_bytes[8:3]);
                                write_payload <= packet_payload[1967:48];
                                timeout_threshold <= watchdog_limit <= 1 ? 32'd0 : watchdog_limit - 32'd1;
                                idle_cycles <= 0;
                                memory_state <= opcode == 8'h04 ? READ_COMMAND : WRITE_COMMAND;
                                packet_state <= MEMORY;
                            end
                        end
                        default: rsp_status <= BAD_CMD;
                    endcase
                end
                REG_REQUEST: if (reg_req_ready) packet_state <= REG_RESPONSE;
                REG_RESPONSE: if (reg_rsp_valid) begin
                    rsp_status <= reg_rsp_status;
                    if (opcode == 8'h02 && reg_rsp_status == OK) begin
                        rsp_length <= 4; rsp_payload[31:0] <= reg_rsp_rdata;
                    end
                    packet_state <= REPLY;
                end
                // Use the first-error register for the wide packet buffer.
                // Immediate fault handling below still stops/drains memory
                // on the detecting edge; an error reply takes one more edge.
                MEMORY: if (host_fatal) begin
                    rsp_status <= host_fatal_code; rsp_length <= 0; rsp_payload <= 0;
                    packet_state <= REPLY;
                end else if (memory_complete && !fault_now) begin
                    rsp_status <= OK; rsp_length <= opcode == 8'h04 ? requested_bytes[8:0] : 9'd0;
                    packet_state <= REPLY;
                end
                REPLY: if (rsp_ready) packet_state <= IDLE;
                default: packet_state <= IDLE;
            endcase

            case (memory_state)
                READ_COMMAND: if (read_command_fire) begin
                    beat_count <= 0; memory_state <= READ_DATA;
                end else if (poisoned) memory_state <= DRAIN;
                WRITE_COMMAND: if (write_command_fire) begin
                    beat_count <= 0; memory_state <= WRITE_DATA;
                end else if (poisoned) memory_state <= DRAIN;
                READ_DATA: if (read_fire) begin
                    if (beat_count < 16) beat_count <= beat_count + 1'b1;
                    // A detecting-edge word stays internal: the registered
                    // fatal branch clears it before any error reply is valid.
                    if (!host_fatal && packet_state == MEMORY && payload_word < 30)
                        rsp_payload[{payload_word,6'b0} +: 64] <= rd_data;
                end
                WRITE_DATA: if (write_fire) begin
                    beat_count <= beat_count + 1'b1;
                    if (beat_count + 5'd1 == burst_beats) memory_state <= WRITE_DONE;
                end
                FINISH: if (poisoned) memory_state <= DRAIN;
                    else if (axi_quiescent) begin memory_complete <= 1'b1; memory_state <= M_IDLE; end
                DRAIN: if (axi_quiescent) memory_state <= M_IDLE;
                default: begin end
            endcase
            if (burst_complete) begin
                if (poisoned) memory_state <= DRAIN;
                else if (words_left == {1'b0,burst_beats}) begin
                    // Completion requires the final read/B terminal followed
                    // by quiescence; only then can ownership and success pass
                    // to software. A blocked quiescence check remains watched.
                    memory_state <= FINISH;
                end else begin
                    memory_address <= next_memory_address;
                    words_left <= next_words_left;
                    burst_beats <= plan_burst(next_memory_address,next_words_left);
                    word_offset <= word_offset + {1'b0,burst_beats};
                    beat_count <= 0;
                    memory_state <= memory_state == READ_DATA ? READ_COMMAND : WRITE_COMMAND;
                end
            end
        end
    end

`ifndef SYNTHESIS
    initial if (DDR_BYTES < 8 || DDR_BYTES > 33'h100000000) $fatal(1,"Unsupported DDR window");
    logic held_rsp, held_reg, held_rd, held_wr, held_data;
    logic [1944:0] prior_rsp;
    logic [52:0] prior_reg;
    logic [52:0] prior_rd, prior_wr;
    logic [71:0] prior_data;
    logic terminal_released_data;
    always @(posedge clk) begin
        if (rst) begin
            held_rsp <= 0; held_reg <= 0; held_rd <= 0; held_wr <= 0; held_data <= 0;
            terminal_released_data <= 0;
        end else begin
            if (held_rsp && (!rsp_valid || {rsp_status,rsp_length,rsp_payload} !== prior_rsp))
                $fatal(1,"Host packet response changed while stalled");
            if (held_reg && (!reg_req_valid || {reg_req_addr,reg_req_write,reg_req_wdata,reg_req_wstrb} !== prior_reg))
                $fatal(1,"Host register request changed while stalled");
            if (held_rd && !poisoned && (!rd_cmd_valid || {rd_cmd_addr,rd_cmd_beats,rd_cmd_tag} !== prior_rd))
                $fatal(1,"Host read command changed while stalled");
            if (held_wr && !poisoned && (!wr_cmd_valid || {wr_cmd_addr,wr_cmd_beats,wr_cmd_tag} !== prior_wr))
                $fatal(1,"Host write command changed while stalled");
            if (held_data && !terminal_released_data &&
                (!wr_data_valid || {wr_data,wr_data_strb} !== prior_data))
                $fatal(1,"Host write data changed before acceptance or terminal cancellation");
            if ((rd_cmd_valid || wr_cmd_valid) &&
                (burst_beats == 0 || burst_beats > 16 || memory_address[2:0] != 0 ||
                 {1'b0,memory_address[11:0]} + {5'd0,burst_beats,3'd0} > 4096))
                $fatal(1,"Host burst geometry invalid");
            if (wr_data_valid && payload_word >= 30) $fatal(1,"Host write payload overflow");
            held_rsp <= rsp_valid && !rsp_ready; prior_rsp <= {rsp_status,rsp_length,rsp_payload};
            held_reg <= reg_req_valid && !reg_req_ready;
            prior_reg <= {reg_req_addr,reg_req_write,reg_req_wdata,reg_req_wstrb};
            held_rd <= rd_cmd_valid && !rd_cmd_ready; prior_rd <= {rd_cmd_addr,rd_cmd_beats,rd_cmd_tag};
            held_wr <= wr_cmd_valid && !wr_cmd_ready; prior_wr <= {wr_cmd_addr,wr_cmd_beats,wr_cmd_tag};
            held_data <= wr_data_valid && !wr_data_ready; prior_data <= {wr_data,wr_data_strb};
            terminal_released_data <= valid_write_terminal;
        end
    end
`endif
endmodule
