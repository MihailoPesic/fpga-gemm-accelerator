`timescale 1ns/1ps
// One-outstanding local register bus. START acknowledges the job controller's
// validated response, not merely receipt of this bus request. Counter inputs
// are frozen job snapshots; a fatal snapshot remains readable during drain.
module gemm_ddr_registers #(
    parameter integer P=4, T=32,
    parameter logic [31:0] BUILD_ID=32'd0, CORE_HZ=32'd100000000,
    parameter logic [31:0] ID=32'h314d474e, VERSION=32'h00010000
) (
    input wire clk, rst,
    input wire req_valid,
    output wire req_ready,
    input wire [15:0] req_addr,
    input wire req_write,
    input wire [31:0] req_wdata,
    input wire [3:0] req_wstrb,
    output wire rsp_valid,
    input wire rsp_ready,
    output logic [31:0] rsp_rdata,
    output logic [15:0] rsp_status,
    output logic [31:0] cfg_job_id, cfg_m, cfg_n, cfg_k,
    output logic [31:0] cfg_a_base, cfg_bt_base, cfg_c_base,
    output logic [31:0] cfg_a_stride, cfg_bt_stride, cfg_c_stride,
    output logic [31:0] cfg_mode, cfg_watchdog,
    input wire job_ready, job_busy, job_done, job_error, ddr_ready, reset_required,
    input wire [15:0] error_code,
    input wire [31:0] last_job_id,
    input wire [63:0] job_cycles, compute_cycles, read_beats, write_beats,
    input wire [63:0] write_valid_bytes, input_wait_cycles, read_stall_cycles, write_stall_cycles,
    output wire job_start_valid,
    input wire job_start_ready,
    input wire job_start_rsp_valid,
    output wire job_start_rsp_ready,
    input wire [15:0] job_start_rsp_status,
    output wire job_clear_status,
    input wire [15:0] job_clear_status_code
);
    localparam logic [15:0] BAD_CMD=16'd1, BAD_ADDR=16'd2, BUSY_CODE=16'd4;
    typedef enum logic [2:0] {IDLE, DECODE, START_SEND, START_WAIT, CLEAR, RESPOND} state_t;
    state_t state;
    logic [15:0] address;
    logic write_request, accepted_busy;
    logic [31:0] write_data;
    logic [3:0] write_strobe;

    assign req_ready = state == IDLE && !rst;
    assign rsp_valid = state == RESPOND && !rst;
    assign job_start_valid = state == START_SEND && !rst;
    assign job_start_rsp_ready = state == START_WAIT && !rst;
    assign job_clear_status = state == CLEAR && !rst;
    wire counter_address = address >= 16'h0080 && address <= 16'h00bc;
    wire known_address = address <= 16'h0050 || counter_address;
    wire config_address = (address >= 16'h0014 && address <= 16'h003c) || address == 16'h004c;
    wire [31:0] status_word = {26'd0,reset_required,ddr_ready,job_error,job_done,job_busy,job_ready && !rst};
    logic [63:0] selected_counter;
    always_comb begin
        case (address[5:3])
            3'd0: selected_counter=job_cycles;
            3'd1: selected_counter=compute_cycles;
            3'd2: selected_counter=read_beats;
            3'd3: selected_counter=write_beats;
            3'd4: selected_counter=write_valid_bytes;
            3'd5: selected_counter=input_wait_cycles;
            3'd6: selected_counter=read_stall_cycles;
            default: selected_counter=write_stall_cycles;
        endcase
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            state <= IDLE;
            address <= 0; write_request <= 0; write_data <= 0; write_strobe <= 0;
            accepted_busy <= 0;
            rsp_rdata <= 0; rsp_status <= 0;
            cfg_job_id <= 0; cfg_m <= 0; cfg_n <= 0; cfg_k <= 0;
            cfg_a_base <= 0; cfg_bt_base <= 0; cfg_c_base <= 0;
            cfg_a_stride <= 0; cfg_bt_stride <= 0; cfg_c_stride <= 0;
            cfg_mode <= 0; cfg_watchdog <= 32'd10000000;
        end else begin
            case (state)
                IDLE: if (req_valid) begin
                    address <= req_addr; write_request <= req_write;
                    write_data <= req_wdata; write_strobe <= req_wstrb;
                    accepted_busy <= job_busy;
                    state <= DECODE;
                end
                DECODE: begin
                    rsp_rdata <= 0; rsp_status <= 0; state <= RESPOND;
                    // Address errors take precedence; malformed writes cannot
                    // reach configuration or either job command interface.
                    if (address[1:0] != 0 || !known_address) rsp_status <= BAD_ADDR;
                    else if (write_request) begin
                        if (write_strobe != 4'hf) rsp_status <= BAD_CMD;
                        else if (config_address) begin
                            if (accepted_busy || job_busy) rsp_status <= BUSY_CODE;
                            else if (address == 16'h004c && write_data == 0) rsp_status <= BAD_CMD;
                            else case (address)
                                16'h0014: cfg_job_id <= write_data;
                                16'h0018: cfg_m <= write_data;
                                16'h001c: cfg_n <= write_data;
                                16'h0020: cfg_k <= write_data;
                                16'h0024: cfg_a_base <= write_data;
                                16'h0028: cfg_bt_base <= write_data;
                                16'h002c: cfg_c_base <= write_data;
                                16'h0030: cfg_a_stride <= write_data;
                                16'h0034: cfg_bt_stride <= write_data;
                                16'h0038: cfg_c_stride <= write_data;
                                // Preserve raw MODE; the serial job validator
                                // reports BAD_DESC for unsupported overlap bits.
                                16'h003c: cfg_mode <= write_data;
                                16'h004c: cfg_watchdog <= write_data;
                                default: rsp_status <= BAD_ADDR;
                            endcase
                        end else if (address == 16'h0010) begin
                            case (write_data)
                                // Busy admission is remembered even if the
                                // old job finishes before this decode edge.
                                32'd1: if (accepted_busy || job_busy) rsp_status <= BUSY_CODE;
                                       else state <= START_SEND;
                                32'd2: if (accepted_busy || job_busy) rsp_status <= BUSY_CODE;
                                       else state <= CLEAR;
                                default: rsp_status <= BAD_CMD;
                            endcase
                        end else rsp_status <= BAD_CMD;
                    end else if (counter_address) begin
                        // Public first-fault counters are frozen even if an
                        // outstanding transfer prevents BUSY from deasserting.
                        if (job_busy && !reset_required) rsp_status <= BUSY_CODE;
                        else rsp_rdata <= address[2] ? selected_counter[63:32] : selected_counter[31:0];
                    end else case (address)
                        16'h0000: rsp_rdata <= ID;
                        16'h0004: rsp_rdata <= VERSION;
                        16'h0008: rsp_rdata <= {16'd256,8'(T),8'(P)};
                        16'h000c: rsp_rdata <= status_word;
                        16'h0010: rsp_rdata <= 0;
                        16'h0014: rsp_rdata <= cfg_job_id;
                        16'h0018: rsp_rdata <= cfg_m;
                        16'h001c: rsp_rdata <= cfg_n;
                        16'h0020: rsp_rdata <= cfg_k;
                        16'h0024: rsp_rdata <= cfg_a_base;
                        16'h0028: rsp_rdata <= cfg_bt_base;
                        16'h002c: rsp_rdata <= cfg_c_base;
                        16'h0030: rsp_rdata <= cfg_a_stride;
                        16'h0034: rsp_rdata <= cfg_bt_stride;
                        16'h0038: rsp_rdata <= cfg_c_stride;
                        16'h003c: rsp_rdata <= cfg_mode;
                        16'h0040: rsp_rdata <= {16'd0,error_code};
                        16'h0044: rsp_rdata <= last_job_id;
                        16'h0048: rsp_rdata <= CORE_HZ;
                        16'h004c: rsp_rdata <= cfg_watchdog;
                        16'h0050: rsp_rdata <= BUILD_ID;
                        default: rsp_status <= BAD_ADDR;
                    endcase
                end
                // If the job becomes busy after decode, its command interface
                // returns BUSY instead of waiting for that job to finish.
                START_SEND: if (job_start_ready) state <= START_WAIT;
                START_WAIT: if (job_start_rsp_valid) begin
                    rsp_status <= job_start_rsp_status;
                    state <= RESPOND;
                end
                CLEAR: begin rsp_status <= job_clear_status_code; state <= RESPOND; end
                RESPOND: if (rsp_ready) state <= IDLE;
                default: state <= IDLE;
            endcase
        end
    end

`ifndef SYNTHESIS
    initial if ((P != 4 && P != 8) || (T != 8 && T != 32))
        $fatal(1,"Unsupported DDR register geometry");
    wire [383:0] config_fields = {cfg_job_id,cfg_m,cfg_n,cfg_k,cfg_a_base,cfg_bt_base,cfg_c_base,
                                  cfg_a_stride,cfg_bt_stride,cfg_c_stride,cfg_mode,cfg_watchdog};
    logic held_req, held_rsp, held_start, previous_clear, previous_busy;
    logic [52:0] previous_request;
    logic [47:0] previous_response;
    logic [383:0] previous_config;
    always @(posedge clk) begin
        if (rst) begin
            held_req <= 0; held_rsp <= 0; held_start <= 0;
            previous_clear <= 0; previous_busy <= 0;
        end else begin
            if (held_req && (!req_valid || {req_addr,req_write,req_wdata,req_wstrb} !== previous_request))
                $fatal(1,"Register request source changed a stalled request");
            if (held_rsp && (!rsp_valid || {rsp_rdata,rsp_status} !== previous_response))
                $fatal(1,"Register response changed while stalled");
            if (held_start && (!job_start_valid || config_fields !== previous_config))
                $fatal(1,"Register START or descriptor changed while stalled");
            if (previous_clear && job_clear_status)
                $fatal(1,"Register CLEAR_STATUS lasted more than one cycle");
            if (previous_busy && config_fields !== previous_config)
                $fatal(1,"Register configuration changed while the job was busy");
            held_req <= req_valid && !req_ready;
            held_rsp <= rsp_valid && !rsp_ready;
            held_start <= job_start_valid && !job_start_ready;
            previous_clear <= job_clear_status; previous_busy <= job_busy;
            previous_request <= {req_addr,req_write,req_wdata,req_wstrb};
            previous_response <= {rsp_rdata,rsp_status}; previous_config <= config_fields;
        end
    end
`endif
endmodule
