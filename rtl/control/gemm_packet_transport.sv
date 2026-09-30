`timescale 1ns/1ps
// Stop-and-wait COBS/CRC transport. All lengths are bytes, low byte first.
// A complete request is validated before req_valid can assert. The request
// fields remain unchanged until the backend response handshake. One received
// frame can wait while a command/reply is active; excess traffic is discarded
// through its delimiter. Memories are intentionally not cleared on reset.
module gemm_packet_transport (
    input wire clk, rst,
    input wire rx_valid, rx_error,
    input wire [7:0] rx_data,
    output wire tx_valid,
    input wire tx_ready,
    output wire [7:0] tx_data,
    output wire req_valid,
    input wire req_ready,
    output logic [7:0] req_opcode,
    output logic [8:0] req_length,
    output logic [2047:0] req_payload,
    input wire rsp_valid,
    output wire rsp_ready,
    input wire [15:0] rsp_status,
    input wire [8:0] rsp_length,
    input wire [1919:0] rsp_payload
);
    localparam [4:0] IDLE=0, DECODE_CODE=1, DECODE_COPY=2, DECODE_ZERO=3,
        HEADER=4, CRC_REQUEST=5, CHECK_CRC=6, COMPARE=7, COPY_PAYLOAD=8,
        ISSUE=9, WAIT_RESPONSE=10, COPY_REQUEST=11, BUILD_RESPONSE=12,
        WRITE_CRC=13, ENCODE_START=14, ENCODE=15, ENCODE_END=16,
        COPY_RESPONSE=17, TRANSMIT=18, DROP=19,
        CRC_REQUEST_FETCH=20, CRC_RESPONSE=21;
    logic [4:0] state;
    logic [7:0] received [0:269];
    logic [8:0] receive_count, frame_length;
    logic frame_ready, dropping;
    wire release_frame = state == HEADER || state == DROP;

    // Independent collector: a locked frame is never overwritten, even when
    // processing releases its buffer halfway through an unwanted new frame.
    always_ff @(posedge clk) begin
        if (rst) begin
            receive_count <= 0;
            frame_length <= 0;
            frame_ready <= 0;
            dropping <= 0;
        end else begin
            if (release_frame) frame_ready <= 0;
            if (rx_valid) begin
                if (frame_ready || dropping) begin
                    dropping <= rx_data != 0;
                end else if (rx_error) begin
                    receive_count <= 0;
                    dropping <= rx_data != 0;
                end else if (rx_data == 0) begin
                    if (receive_count != 0) begin
                        frame_length <= receive_count;
                        frame_ready <= 1;
                    end
                    receive_count <= 0;
                end else if (receive_count < 270) begin
                    received[receive_count] <= rx_data;
                    receive_count <= receive_count + 1'b1;
                end else begin
                    receive_count <= 0;
                    dropping <= 1;
                end
            end
        end
    end

    logic [7:0] decoded [0:265];
    logic [7:0] last_request [0:265];
    logic [7:0] response_raw [0:251];
    logic [7:0] response_encoded [0:252];
    logic [7:0] last_response [0:252];
    logic [8:0] decode_read, decoded_count, index;
    logic [7:0] decode_left, decode_code;
    logic [15:0] packet_sequence, last_sequence;
    logic cache_valid, replace_cache, replay;
    logic [8:0] last_request_length, last_response_length;
    logic [31:0] crc;
    logic [7:0] crc_input;
    logic [15:0] response_status;
    logic [8:0] response_length, raw_length, encoded_length;
    logic [8:0] encode_read, encode_write, code_position;
    logic [7:0] encode_code;
    wire [8:0] transmit_length = replay ? last_response_length : encoded_length;
    logic [7:0] header_version, header_opcode;
    logic [15:0] header_sequence, declared_length;
    logic [31:0] received_crc;
    wire decoded_write = decoded_count < 266 &&
                         (state == DECODE_COPY || state == DECODE_ZERO);
    wire [7:0] decoded_write_byte = state == DECODE_COPY ? received[decode_read] : 8'd0;
    wire [8:0] decoded_address = state == COPY_PAYLOAD ? index+9'd6 : index;
    wire [7:0] decoded_byte = decoded[decoded_address];

    // One shared asynchronous read port permits distributed RAM inference.
    // Header/trailer registers avoid separate variable reads of the same RAM.
    // The rolling trailer contains the last four decoded bytes, little-endian.
    always_ff @(posedge clk) begin
        if (decoded_write && !rst) decoded[decoded_count] <= decoded_write_byte;
        if (rst) begin
            header_version <= 0;
            header_opcode <= 0;
            header_sequence <= 0;
            declared_length <= 0;
            received_crc <= 0;
        end else if (decoded_write) begin
            received_crc <= {decoded_write_byte, received_crc[31:8]};
            case (decoded_count)
                0: header_version <= decoded_write_byte;
                1: header_opcode <= decoded_write_byte;
                2: header_sequence[7:0] <= decoded_write_byte;
                3: header_sequence[15:8] <= decoded_write_byte;
                4: declared_length[7:0] <= decoded_write_byte;
                5: declared_length[15:8] <= decoded_write_byte;
                default: begin end
            endcase
        end
    end

    function automatic [31:0] crc_byte(input [31:0] prior, input [7:0] value);
        reg [31:0] work;
        integer bit_number;
        begin
            work = prior ^ {24'b0, value};
            for (bit_number=0; bit_number<8; bit_number=bit_number+1)
                work = work[0] ? ((work >> 1) ^ 32'hedb88320) : (work >> 1);
            crc_byte = work;
        end
    endfunction

    logic [7:0] response_byte;
    always_comb begin
        case (index)
            0: response_byte = 1;
            1: response_byte = req_opcode | 8'h80;
            2: response_byte = packet_sequence[7:0];
            3: response_byte = packet_sequence[15:8];
            4: response_byte = response_length[7:0] + 8'd2;
            5: response_byte = 0;
            6: response_byte = response_status[7:0];
            7: response_byte = response_status[15:8];
            default: response_byte = 0;
        endcase
        if (index >= 8 && index < response_length+8)
            response_byte = rsp_payload[(index-8)*8 +: 8];
    end

    assign req_valid = state == ISSUE;
    // Keep the backend response stable while its bytes are copied into the
    // raw response buffer; avoid a second 1920-bit payload register bank.
    assign rsp_ready = state == BUILD_RESPONSE && replace_cache && index+1'b1 == raw_length-4;
    assign tx_valid = state == TRANSMIT;
    assign tx_data = index == transmit_length ? 8'b0 :
                     (replay ? last_response[index] : response_encoded[index]);

    always_ff @(posedge clk) begin
        if (rst) begin
            state <= IDLE;
            req_opcode <= 0;
            req_length <= 0;
            req_payload <= 0;
            cache_valid <= 0;
            replace_cache <= 0;
            replay <= 0;
            index <= 0;
            packet_sequence <= 0;
            last_sequence <= 0;
            last_request_length <= 0;
            last_response_length <= 0;
            encoded_length <= 0;
        end else begin
            case (state)
                IDLE: if (frame_ready) begin
                    decode_read <= 0;
                    decoded_count <= 0;
                    state <= DECODE_CODE;
                end
                DECODE_CODE: begin
                    if (decode_read >= frame_length || received[decode_read] == 0 ||
                        {1'b0, received[decode_read]} > frame_length-decode_read) begin
                        state <= DROP;
                    end else begin
                        decode_code <= received[decode_read];
                        decode_left <= received[decode_read] - 1'b1;
                        decode_read <= decode_read + 1'b1;
                        if (received[decode_read] != 1) state <= DECODE_COPY;
                        else if (decode_read+1'b1 == frame_length) state <= HEADER;
                        else state <= DECODE_ZERO;
                    end
                end
                DECODE_COPY: begin
                    if (decoded_count == 266) state <= DROP;
                    else begin
                        decoded_count <= decoded_count + 1'b1;
                        decode_read <= decode_read + 1'b1;
                        decode_left <= decode_left - 1'b1;
                        if (decode_left == 1) begin
                            if (decode_read+1'b1 == frame_length) state <= HEADER;
                            else if (decode_code == 255) state <= DECODE_CODE;
                            else state <= DECODE_ZERO;
                        end
                    end
                end
                DECODE_ZERO: begin
                    if (decoded_count == 266) state <= DROP;
                    else begin
                        decoded_count <= decoded_count + 1'b1;
                        state <= DECODE_CODE;
                    end
                end
                HEADER: begin
                    if (decoded_count < 10 || header_version != 1 || declared_length > 256 ||
                        {7'b0, decoded_count} != declared_length+16'd10) state <= IDLE;
                    else begin
                        crc <= 32'hffffffff;
                        index <= 0;
                        state <= CRC_REQUEST_FETCH;
                    end
                end
                CRC_REQUEST_FETCH: begin
                    crc_input <= decoded_byte;
                    state <= CRC_REQUEST;
                end
                CRC_REQUEST: begin
                    crc <= crc_byte(crc, crc_input);
                    index <= index + 1'b1;
                    if (index+1'b1 == decoded_count-4) state <= CHECK_CRC;
                    else state <= CRC_REQUEST_FETCH;
                end
                CHECK_CRC: begin
                    if (received_crc != ~crc) state <= IDLE;
                    else begin
                        req_opcode <= header_opcode;
                        req_length <= declared_length[8:0];
                        packet_sequence <= header_sequence;
                        index <= 0;
                        replay <= 0;
                        replace_cache <= 0;
                        if (cache_valid && header_sequence == last_sequence)
                            state <= COMPARE;
                        else begin
                            req_payload <= 0;
                            state <= COPY_PAYLOAD;
                        end
                    end
                end
                COMPARE: begin
                    if (last_request_length != decoded_count || decoded_byte != last_request[index]) begin
                        response_status <= 16'd6;
                        response_length <= 0;
                        raw_length <= 12;
                        crc <= 32'hffffffff;
                        index <= 0;
                        state <= BUILD_RESPONSE;
                    end else if (index+1'b1 == decoded_count) begin
                        replay <= 1;
                        index <= 0;
                        state <= TRANSMIT;
                    end else index <= index + 1'b1;
                end
                COPY_PAYLOAD: begin
                    if (index == req_length) state <= ISSUE;
                    else begin
                        req_payload[index*8 +: 8] <= decoded_byte;
                        index <= index + 1'b1;
                    end
                end
                ISSUE: if (req_ready) state <= WAIT_RESPONSE;
                WAIT_RESPONSE: if (rsp_valid) begin
                    // The backend contract limits data to 240 bytes. Guard the
                    // buffer even if a faulty backend violates that contract.
                    response_status <= rsp_length <= 240 ? rsp_status : 16'd1;
                    response_length <= rsp_length <= 240 ? rsp_length : 9'd0;
                    raw_length <= rsp_length <= 240 ? rsp_length+9'd12 : 9'd12;
                    replace_cache <= 1;
                    cache_valid <= 0;
                    last_sequence <= packet_sequence;
                    last_request_length <= decoded_count;
                    index <= 0;
                    state <= COPY_REQUEST;
                end
                COPY_REQUEST: begin
                    last_request[index] <= decoded_byte;
                    if (index+1'b1 == decoded_count) begin
                        index <= 0;
                        crc <= 32'hffffffff;
                        state <= BUILD_RESPONSE;
                    end else index <= index + 1'b1;
                end
                BUILD_RESPONSE: begin
                    response_raw[index] <= response_byte;
                    crc_input <= response_byte;
                    state <= CRC_RESPONSE;
                end
                CRC_RESPONSE: begin
                    crc <= crc_byte(crc, crc_input);
                    if (index+1'b1 == raw_length-4) begin
                        index <= 0;
                        state <= WRITE_CRC;
                    end else begin
                        index <= index + 1'b1;
                        state <= BUILD_RESPONSE;
                    end
                end
                WRITE_CRC: begin
                    response_raw[raw_length-4+index] <= ~(crc >> (index*8));
                    if (index == 3) state <= ENCODE_START;
                    else index <= index + 1'b1;
                end
                ENCODE_START: begin
                    encode_read <= 0;
                    encode_write <= 1;
                    code_position <= 0;
                    encode_code <= 1;
                    state <= ENCODE;
                end
                ENCODE: begin
                    if (response_raw[encode_read] == 0) begin
                        response_encoded[code_position] <= encode_code;
                        code_position <= encode_write;
                        encode_code <= 1;
                    end else begin
                        response_encoded[encode_write] <= response_raw[encode_read];
                        encode_code <= encode_code + 1'b1;
                    end
                    encode_read <= encode_read + 1'b1;
                    encode_write <= encode_write + 1'b1;
                    if (encode_read+1'b1 == raw_length) state <= ENCODE_END;
                end
                ENCODE_END: begin
                    response_encoded[code_position] <= encode_code;
                    encoded_length <= encode_write;
                    index <= 0;
                    state <= replace_cache ? COPY_RESPONSE : TRANSMIT;
                end
                COPY_RESPONSE: begin
                    last_response[index] <= response_encoded[index];
                    if (index+1'b1 == encoded_length) begin
                        last_response_length <= encoded_length;
                        cache_valid <= 1;
                        index <= 0;
                        state <= TRANSMIT;
                    end else index <= index + 1'b1;
                end
                TRANSMIT: if (tx_ready) begin
                    if (index == transmit_length) state <= IDLE;
                    else index <= index + 1'b1;
                end
                DROP: state <= IDLE;
                default: state <= IDLE;
            endcase
        end
    end
endmodule
