`timescale 1ns/1ps
// BRAM-preview command backend. Framing/CRC/retries belong to the transport.
// req_payload stays stable from request acceptance through response acceptance.
module gemm_preview_controller #(
    parameter logic [31:0] BUILD_ID = 32'd0,
    parameter integer CORE_HZ = 100_000_000
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
    output wire busy,
    output logic done, error
);
    localparam logic [31:0] ID = 32'h3142474e, VERSION = 32'h00000100;
    localparam logic [15:0] OK=0, BAD_CMD=1, BAD_ADDR=2, BAD_DESC=3,
                            BUSY=4, NOT_READY=5, PROTOCOL=8;
    typedef enum logic [3:0] {IDLE, DECODE, LOAD, READ_REQUEST, READ_RESPONSE,
                             START_JOB, CHECK_START, REPLY} state_t;
    state_t state;
    logic [7:0] opcode;
    logic [8:0] packet_length;
    logic [15:0] register_address, memory_length;
    logic [31:0] write_value, memory_address;
    logic [4:0] word_index;
    logic [31:0] cfg_m, cfg_n, cfg_k, cfg_job_id, last_job_id, error_code;
    logic [5:0] result_m, result_n;
    logic job_active, protocol_fault;
    logic [63:0] frozen_job_cycles, frozen_compute_cycles;
    wire engine_busy, engine_done, engine_error, engine_start_ready;
    wire engine_load_ready, engine_read_ready, engine_response_valid, engine_response_error;
    wire [1:0] engine_result_valid;
    wire [63:0] engine_job_cycles, engine_compute_cycles, engine_response_data;
    wire [7:0] engine_response_strb;
    wire [31:0] current_address = memory_address + {24'd0, word_index, 3'b000};
    wire [16:0] next_byte_count = ({12'd0, word_index} + 17'd1) << 3;
    wire [16:0] write_row_end = {9'd0, memory_address[7:0]} + {1'b0, memory_length};
    wire [16:0] read_row_end = {10'd0, memory_address[6:0]} + {1'b0, memory_length};
    wire [16:0] result_row_bytes = (({11'd0, result_n} + 17'd1) >> 1) << 3;
    wire legal_dimensions = cfg_m >= 1 && cfg_m <= 32 && cfg_n >= 1 &&
                            cfg_n <= 32 && cfg_k >= 1 && cfg_k <= 256;
    wire engine_start = state == START_JOB && engine_start_ready && !rst;
    assign busy = engine_busy || job_active || state == START_JOB;
    assign req_ready = state == IDLE && !rst;
    assign rsp_valid = state == REPLY && !rst;

    gemm_tile_engine #(.P(4), .T(32)) engine (
        .clk(clk), .rst(rst),
        .load_valid(state == LOAD && !rst), .load_ready(engine_load_ready),
        .load_bt(current_address[13]), .load_buf(1'b0),
        .load_q(current_address[12:8]), .load_word(current_address[7:3]),
        .load_data(req_payload[48 + word_index*64 +: 64]),
        .start(engine_start), .start_ready(engine_start_ready),
        .input_buf(1'b0), .output_buf(1'b0), .m(cfg_m[5:0]), .n(cfg_n[5:0]), .k(cfg_k[8:0]),
        .busy(engine_busy), .done(engine_done), .cmd_error(engine_error),
        .result_valid(engine_result_valid), .job_cycles(engine_job_cycles),
        .compute_cycles(engine_compute_cycles), .microtiles(),
        .read_valid(state == READ_REQUEST && !rst), .read_ready(engine_read_ready),
        .read_buf(1'b0), .read_row(current_address[11:7]), .read_pair(current_address[6:3]),
        .response_valid(engine_response_valid), .response_ready(state == READ_RESPONSE && !rst),
        .response_data(engine_response_data), .response_strb(engine_response_strb),
        .response_error(engine_response_error)
    );

    always_ff @(posedge clk) begin
        if (rst) begin
            state <= IDLE;
            opcode <= '0;
            packet_length <= '0;
            register_address <= '0;
            memory_length <= '0;
            write_value <= '0;
            memory_address <= '0;
            word_index <= '0;
            cfg_m <= '0;
            cfg_n <= '0;
            cfg_k <= '0;
            cfg_job_id <= '0;
            last_job_id <= '0;
            error_code <= '0;
            result_m <= '0;
            result_n <= '0;
            job_active <= 1'b0;
            protocol_fault <= 1'b0;
            frozen_job_cycles <= '0;
            frozen_compute_cycles <= '0;
            done <= 1'b0;
            error <= 1'b0;
            rsp_status <= OK;
            rsp_length <= '0;
            rsp_payload <= '0;
        end else begin
            if (job_active && engine_done && !engine_busy) begin
                job_active <= 1'b0;
                if (!protocol_fault && !engine_error) begin
                    done <= 1'b1;
                    frozen_job_cycles <= engine_job_cycles;
                    frozen_compute_cycles <= engine_compute_cycles;
                end
            end
            // Unexpected core rejection is an implementation fault, not a
            // request-validation error. Reset is required; an in-flight local
            // job may finish internally but must never publish successful data.
            if (engine_error) begin
                protocol_fault <= 1'b1;
                error <= 1'b1;
                error_code <= {16'd0, PROTOCOL};
                done <= 1'b0;
            end
            case (state)
                IDLE: if (req_valid) begin
                    opcode <= req_opcode;
                    packet_length <= req_length;
                    register_address <= req_payload[15:0];
                    write_value <= req_payload[47:16];
                    memory_address <= req_payload[31:0];
                    memory_length <= req_payload[47:32];
                    word_index <= '0;
                    state <= DECODE;
                end
                DECODE: begin
                    rsp_status <= OK;
                    rsp_length <= '0;
                    rsp_payload <= '0;
                    state <= REPLY;
                    case (opcode)
                        8'h01: if (packet_length != 4) rsp_status <= BAD_CMD;
                            else begin
                                rsp_length <= 12;
                                rsp_payload[95:0] <= {VERSION, ID, req_payload[31:0]};
                            end
                        8'h02: if (packet_length != 2) rsp_status <= BAD_CMD;
                            else if (register_address[1:0] != 0) rsp_status <= BAD_ADDR;
                            else begin
                                rsp_length <= 4;
                                case (register_address)
                                    16'h00: rsp_payload[31:0] <= ID;
                                    16'h04: rsp_payload[31:0] <= VERSION;
                                    16'h08: rsp_payload[31:0] <= 32'h01002004;
                                    16'h0c: rsp_payload[31:0] <= {26'd0, protocol_fault, 1'b0, error, done, busy, !busy && !protocol_fault};
                                    16'h10: rsp_payload[31:0] <= 32'd0;
                                    16'h14: rsp_payload[31:0] <= cfg_job_id;
                                    16'h18: rsp_payload[31:0] <= cfg_m;
                                    16'h1c: rsp_payload[31:0] <= cfg_n;
                                    16'h20: rsp_payload[31:0] <= cfg_k;
                                    16'h24: rsp_payload[31:0] <= 32'h00000000;
                                    16'h28: rsp_payload[31:0] <= 32'h00002000;
                                    16'h2c: rsp_payload[31:0] <= 32'h00004000;
                                    16'h30, 16'h34: rsp_payload[31:0] <= 32'd256;
                                    16'h38: rsp_payload[31:0] <= 32'd128;
                                    16'h3c: rsp_payload[31:0] <= 32'd0;
                                    16'h40: rsp_payload[31:0] <= error_code;
                                    16'h44: rsp_payload[31:0] <= last_job_id;
                                    16'h48: rsp_payload[31:0] <= CORE_HZ;
                                    16'h50: rsp_payload[31:0] <= BUILD_ID;
                                    16'h80: rsp_payload[31:0] <= frozen_job_cycles[31:0];
                                    16'h84: rsp_payload[31:0] <= frozen_job_cycles[63:32];
                                    16'h88: rsp_payload[31:0] <= frozen_compute_cycles[31:0];
                                    16'h8c: rsp_payload[31:0] <= frozen_compute_cycles[63:32];
                                    default: begin rsp_status <= BAD_ADDR; rsp_length <= 0; end
                                endcase
                            end
                        8'h03: if (packet_length != 6) rsp_status <= BAD_CMD;
                            else if (register_address[1:0] != 0) rsp_status <= BAD_ADDR;
                            else case (register_address)
                                16'h10: if (write_value != 1 && write_value != 2) rsp_status <= BAD_CMD;
                                    else if (busy) rsp_status <= BUSY;
                                    else if (protocol_fault) rsp_status <= PROTOCOL;
                                    else if (write_value == 2) begin
                                        done <= 1'b0;
                                        error <= 1'b0;
                                        error_code <= '0;
                                    end else if (!legal_dimensions) begin
                                        rsp_status <= BAD_DESC;
                                        error <= 1'b1;
                                        error_code <= {16'd0, BAD_DESC};
                                    end else if (!engine_start_ready) rsp_status <= NOT_READY;
                                    else state <= START_JOB;
                                16'h14, 16'h18, 16'h1c, 16'h20: if (busy) rsp_status <= BUSY;
                                    else if (protocol_fault) rsp_status <= PROTOCOL;
                                    else case (register_address)
                                        16'h14: cfg_job_id <= write_value;
                                        16'h18: cfg_m <= write_value;
                                        16'h1c: cfg_n <= write_value;
                                        16'h20: cfg_k <= write_value;
                                        default: begin end
                                    endcase
                                16'h3c: if (busy) rsp_status <= BUSY;
                                    else if (protocol_fault) rsp_status <= PROTOCOL;
                                    else if (write_value != 0) rsp_status <= BAD_CMD;
                                16'h00, 16'h04, 16'h08, 16'h0c, 16'h24, 16'h28, 16'h2c,
                                16'h30, 16'h34, 16'h38, 16'h40, 16'h44, 16'h48, 16'h50,
                                16'h80, 16'h84, 16'h88, 16'h8c: rsp_status <= BAD_CMD;
                                default: rsp_status <= BAD_ADDR;
                            endcase
                        8'h04, 8'h05: begin
                            // All bounds are checked before entering either
                            // transfer state. Widen sums before comparing.
                            if (memory_length < 8 || memory_length > 240 || memory_length[2:0] != 0 ||
                                (opcode == 4 && packet_length != 6) ||
                                (opcode == 5 && {8'd0, packet_length} != 17'd6 + {1'b0, memory_length}))
                                rsp_status <= BAD_CMD;
                            else if (memory_address[2:0] != 0) rsp_status <= BAD_ADDR;
                            else if (opcode == 5) begin
                                if (memory_address >= 32'h4000 || write_row_end > 256) rsp_status <= BAD_ADDR;
                                else if (busy) rsp_status <= BUSY;
                                else if (protocol_fault) rsp_status <= PROTOCOL;
                                else state <= LOAD;
                            end else begin
                                if (memory_address < 32'h4000 || memory_address >= 32'h5000) rsp_status <= BAD_ADDR;
                                else if (busy) rsp_status <= BUSY;
                                else if (protocol_fault) rsp_status <= PROTOCOL;
                                else if (!engine_result_valid[0]) rsp_status <= NOT_READY;
                                else if ({1'b0, memory_address[11:7]} >= result_m || read_row_end > result_row_bytes)
                                    rsp_status <= BAD_ADDR;
                                else begin
                                    rsp_length <= memory_length[8:0];
                                    state <= READ_REQUEST;
                                end
                            end
                        end
                        default: rsp_status <= BAD_CMD;
                    endcase
                end
                LOAD: if (engine_load_ready) begin
                    if (next_byte_count == {1'b0, memory_length}) state <= REPLY;
                    else word_index <= word_index + 1'b1;
                end
                READ_REQUEST: if (engine_read_ready) state <= READ_RESPONSE;
                READ_RESPONSE: if (engine_response_valid) begin
                    if (engine_response_error || (engine_response_strb != 8'hff && engine_response_strb != 8'h0f)) begin
                        rsp_status <= PROTOCOL;
                        rsp_length <= '0;
                        rsp_payload <= '0;
                        error <= 1'b1;
                        protocol_fault <= 1'b1;
                        done <= 1'b0;
                        error_code <= {16'd0, PROTOCOL};
                        state <= REPLY;
                    end else begin
                        rsp_payload[word_index*64 +: 64] <= engine_response_data;
                        if (next_byte_count == {1'b0, memory_length}) state <= REPLY;
                        else begin word_index <= word_index + 1'b1; state <= READ_REQUEST; end
                    end
                end
                START_JOB: begin
                    if (engine_start_ready) begin
                        job_active <= 1'b1;
                        last_job_id <= cfg_job_id;
                        result_m <= cfg_m[5:0];
                        result_n <= cfg_n[5:0];
                        done <= 1'b0;
                        error <= 1'b0;
                        error_code <= '0;
                        state <= CHECK_START;
                    end else begin
                        rsp_status <= PROTOCOL;
                        error <= 1'b1;
                        protocol_fault <= 1'b1;
                        done <= 1'b0;
                        error_code <= {16'd0, PROTOCOL};
                        state <= REPLY;
                    end
                end
                CHECK_START: begin
                    if (engine_error) begin rsp_status <= PROTOCOL; job_active <= 1'b0; end
                    state <= REPLY;
                end
                REPLY: if (rsp_ready) state <= IDLE;
                default: state <= IDLE;
            endcase
        end
    end
endmodule
