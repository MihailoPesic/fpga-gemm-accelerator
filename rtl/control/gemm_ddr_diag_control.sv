`timescale 1ns/1ps
// Packet backend for the bounded DDR diagnostic; no host memory access.
module gemm_ddr_diag_control #(
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
    input wire ddr_ready, busy, done, error,
    input wire [15:0] error_code,
    input wire [31:0] first_fail_addr,
    input wire [63:0] expected, actual, cycles, read_beats, write_beats,
    output logic start,
    output logic [31:0] seed
);
    localparam logic [31:0] ID=32'h3144474e, VERSION=32'h00000100;
    localparam logic [15:0] OK=0, BAD_CMD=1, BAD_ADDR=2,
                            BUSY=4, NOT_READY=5, PROTOCOL=8;
    typedef enum logic [1:0] {IDLE, DECODE, REPLY} state_t;
    state_t state;
    logic [7:0] opcode;
    logic [8:0] packet_length;
    logic [15:0] address;
    logic [31:0] write_value, request_word;
    logic start_pending;
    wire active = busy || start_pending;
    wire ready = !active && ddr_ready && !error;
    wire diagnostic_address = address == 16'h60 || address == 16'h68 ||
        address == 16'h6c || address == 16'h70 || address == 16'h74 ||
        address == 16'h80 || address == 16'h84 || address == 16'h90 ||
        address == 16'h94 || address == 16'h98 || address == 16'h9c;
    assign req_ready = state == IDLE && !rst;
    assign rsp_valid = state == REPLY && !rst;

    always_ff @(posedge clk) begin
        start <= 1'b0;
        if (rst) begin
            state <= IDLE;
            opcode <= '0;
            packet_length <= '0;
            address <= '0;
            write_value <= '0;
            request_word <= '0;
            seed <= 32'h12345678;
            start_pending <= 1'b0;
            rsp_status <= OK;
            rsp_length <= '0;
            rsp_payload <= '0;
        end else begin
            if (busy || error) start_pending <= 1'b0;
            case (state)
                IDLE: if (req_valid) begin
                    opcode <= req_opcode;
                    packet_length <= req_length;
                    address <= req_payload[15:0];
                    write_value <= req_payload[47:16];
                    request_word <= req_payload[31:0];
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
                                rsp_payload[95:0] <= {VERSION, ID, request_word};
                            end
                        8'h02: if (packet_length != 2) rsp_status <= BAD_CMD;
                            else if (address[1:0] != 0) rsp_status <= BAD_ADDR;
                            else if (diagnostic_address && active && !error) rsp_status <= BUSY;
                            else begin
                                rsp_length <= 4;
                                case (address)
                                    16'h00: rsp_payload[31:0] <= ID;
                                    16'h04: rsp_payload[31:0] <= VERSION;
                                    16'h0c: rsp_payload[31:0] <= {26'd0, error, ddr_ready, error,
                                                                    done && !active, active, ready};
                                    16'h10: rsp_payload[31:0] <= 0;
                                    16'h14: rsp_payload[31:0] <= seed;
                                    16'h40: rsp_payload[31:0] <= {16'd0, error_code};
                                    16'h48: rsp_payload[31:0] <= CORE_HZ;
                                    16'h50: rsp_payload[31:0] <= BUILD_ID;
                                    16'h60: rsp_payload[31:0] <= first_fail_addr;
                                    16'h68: rsp_payload[31:0] <= expected[31:0];
                                    16'h6c: rsp_payload[31:0] <= expected[63:32];
                                    16'h70: rsp_payload[31:0] <= actual[31:0];
                                    16'h74: rsp_payload[31:0] <= actual[63:32];
                                    16'h80: rsp_payload[31:0] <= cycles[31:0];
                                    16'h84: rsp_payload[31:0] <= cycles[63:32];
                                    16'h90: rsp_payload[31:0] <= read_beats[31:0];
                                    16'h94: rsp_payload[31:0] <= read_beats[63:32];
                                    16'h98: rsp_payload[31:0] <= write_beats[31:0];
                                    16'h9c: rsp_payload[31:0] <= write_beats[63:32];
                                    default: begin rsp_status <= BAD_ADDR; rsp_length <= 0; end
                                endcase
                            end
                        8'h03: if (packet_length != 6) rsp_status <= BAD_CMD;
                            else if (address[1:0] != 0) rsp_status <= BAD_ADDR;
                            else case (address)
                                16'h10: if (write_value != 1) rsp_status <= BAD_CMD;
                                    else if (error) rsp_status <= PROTOCOL;
                                    else if (active) rsp_status <= BUSY;
                                    else if (!ddr_ready) rsp_status <= NOT_READY;
                                    else begin start <= 1'b1; start_pending <= 1'b1; end
                                16'h14: if (error) rsp_status <= PROTOCOL;
                                    else if (active) rsp_status <= BUSY;
                                    else seed <= write_value;
                                16'h00, 16'h04, 16'h0c, 16'h40, 16'h48, 16'h50,
                                16'h60, 16'h68, 16'h6c, 16'h70, 16'h74, 16'h80,
                                16'h84, 16'h90, 16'h94, 16'h98, 16'h9c: rsp_status <= BAD_CMD;
                                default: rsp_status <= BAD_ADDR;
                            endcase
                        default: rsp_status <= BAD_CMD;
                    endcase
                end
                REPLY: if (rsp_ready) state <= IDLE;
                default: state <= IDLE;
            endcase
        end
    end
endmodule
