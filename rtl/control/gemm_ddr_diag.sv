`timescale 1ns/1ps
// Bounded destructive DDR test. All sixteen slots are initialized before any
// reads, so a high-address alias cannot pass by reading each write immediately.
// The burst engine is external; rst must reset the complete AXI platform.
module gemm_ddr_diag #(
    parameter integer WATCHDOG_LIMIT = 10000000
) (
    input wire clk, rst, start, ddr_ready,
    input wire [31:0] seed,
    output logic busy, done, error,
    output logic [15:0] error_code,
    output logic [31:0] first_fail_addr,
    output logic [63:0] expected, actual,
    output logic [63:0] cycles, read_beats, write_beats,
    input wire engine_fatal,
    input wire [15:0] engine_fatal_code,
    input wire engine_progress, axi_quiescent,
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
    localparam logic [15:0] PROTOCOL=8, WATCHDOG=9, CALIB_LOST=10, MISMATCH=16'h0100;
    typedef enum logic [3:0] {IDLE, PREPARE, WRITE_CMD, WRITE_DATA, WRITE_DONE,
                             READ_CMD, READ_DATA, READ_DONE, DRAIN} state_t;
    typedef enum logic [1:0] {INITIALIZE, CHECK_BASE, OVERLAY, CHECK_OVERLAY} phase_t;
    state_t state;
    phase_t phase;
    logic [3:0] slot, write_index;
    logic half;
    logic [4:0] read_count, command_beats;
    logic [31:0] seed_latched, command_address, watchdog_count;
    logic [15:0] command_tag;
    logic check_pending, check_shape_error;
    logic [31:0] check_address;
    logic [63:0] check_expected, check_actual;

    function automatic [31:0] slot_address(input logic [3:0] number);
        case (number)
            0: slot_address=32'h00000000; 1: slot_address=32'h00001000;
            2: slot_address=32'h00010000; 3: slot_address=32'h00080000;
            4: slot_address=32'h00100000; 5: slot_address=32'h00200000;
            6: slot_address=32'h00400000; 7: slot_address=32'h00800000;
            8: slot_address=32'h01000000; 9: slot_address=32'h02000000;
           10: slot_address=32'h04000000;11: slot_address=32'h06000000;
           12: slot_address=32'h07000000;13: slot_address=32'h07800000;
           14: slot_address=32'h07f00000;15: slot_address=32'h07ffff00;
        endcase
    endfunction

    function automatic [63:0] base_pattern(input logic [31:0] address, value_seed);
        base_pattern = {address ^ value_seed ^ 32'h3c6ef372,
                        {address[24:0],address[31:25]} ^ ~value_seed ^ 32'ha5c39e17};
    endfunction

    function automatic [7:0] byte_mask(input logic [2:0] selector);
        case (selector)
            0: byte_mask=8'h0f; 1: byte_mask=8'hf0;
            2: byte_mask=8'h55; 3: byte_mask=8'haa;
            4: byte_mask=8'h01; 5: byte_mask=8'h80;
            6: byte_mask=8'h00; 7: byte_mask=8'hff;
        endcase
    endfunction

    wire [31:0] write_address = command_address + {25'd0,write_index,3'd0};
    wire [31:0] read_address = command_address + {24'd0,read_count,3'd0};
    wire [63:0] original_word = base_pattern(read_address,seed_latched);
    wire [4:0] slot_word = {half,read_count[3:0]};
    wire [2:0] overlay_selector = slot[2:0] + slot_word[2:0] - 3'd1;
    wire [7:0] overlay_mask = byte_mask(overlay_selector);
    logic [63:0] expected_word;
    integer lane;
    always_comb begin
        expected_word = original_word;
        if (phase == CHECK_OVERLAY && slot_word >= 1 && slot_word <= {1'b0,slot}+5'd1)
            for (lane=0; lane<8; lane=lane+1)
                if (overlay_mask[lane]) expected_word[lane*8+:8] = ~original_word[lane*8+:8];
    end

    // An offered command/data item remains valid after an error. Only the
    // engine's explicit rejected-write completion can cancel local write data.
    assign rd_cmd_valid = state == READ_CMD && !rst;
    assign wr_cmd_valid = state == WRITE_CMD && !rst;
    assign rd_cmd_addr = command_address;
    assign wr_cmd_addr = command_address;
    assign rd_cmd_beats = command_beats;
    assign wr_cmd_beats = command_beats;
    assign rd_cmd_tag = command_tag;
    assign wr_cmd_tag = command_tag;
    assign wr_data_valid = state == WRITE_DATA && !rst;
    assign wr_data = phase == OVERLAY ? ~base_pattern(write_address,seed_latched) :
                                       base_pattern(write_address,seed_latched);
    assign wr_data_strb = phase == OVERLAY ? byte_mask(slot[2:0]+write_index[2:0]) : 8'hff;
    // Capture the pattern and incoming word before comparing them. This splits
    // address/overlay generation from the wide comparator and fault fanout.
    // One pending check backpressures both data and completion: even the final
    // word must be checked before a burst can advance or the job can report DONE.
    // After a fault the remaining data still drains, independent of calibration.
    assign rd_data_ready = !rst && !check_pending;
    assign rd_done_ready = !rst && !check_pending;
    assign wr_done_ready = !rst;
    wire rd_command_fire = rd_cmd_valid && rd_cmd_ready;
    wire wr_command_fire = wr_cmd_valid && wr_cmd_ready;
    wire read_fire = rd_data_valid && rd_data_ready;
    wire write_fire = wr_data_valid && wr_data_ready;
    wire rd_done_fire = rd_done_valid && rd_done_ready;
    wire wr_done_fire = wr_done_valid && wr_done_ready;
    wire incoming_shape_error = state != READ_DATA || rd_data_tag != command_tag ||
        rd_data_index != read_count[3:0] || rd_data_last != (read_count+5'd1 == command_beats);
    wire read_shape_error = check_pending && check_shape_error;
    wire rd_completion_error = rd_done_fire && (rd_done_tag != command_tag ||
        (state != READ_DONE && !(state == READ_DATA && rd_done_status != 0)));
    wire wr_completion_error = wr_done_fire && (wr_done_tag != command_tag ||
        (state != WRITE_DONE && !(state == WRITE_DATA && wr_done_status != 0)));
    wire compare_error = check_pending && !check_shape_error && check_actual != check_expected;
    wire any_progress = engine_progress || rd_command_fire || wr_command_fire || read_fire ||
                        write_fire || rd_done_fire || wr_done_fire || check_pending || state == PREPARE;
    logic [15:0] fault_code;
    always_comb begin
        fault_code = 0;
        if ((busy || (start && !error)) && !ddr_ready) fault_code = CALIB_LOST;
        else if (engine_fatal) fault_code = engine_fatal_code == 0 ? PROTOCOL : engine_fatal_code;
        else if (read_shape_error || rd_completion_error || wr_completion_error) fault_code = PROTOCOL;
        else if (rd_done_fire && rd_done_status != 0) fault_code = rd_done_status;
        else if (wr_done_fire && wr_done_status != 0) fault_code = wr_done_status;
        else if (compare_error) fault_code = MISMATCH;
        else if (busy && !any_progress && watchdog_count >= WATCHDOG_LIMIT-1) fault_code = WATCHDOG;
    end
    wire failed = error || fault_code != 0;
    wire command_success = !failed && ((state == WRITE_DONE && wr_done_fire) ||
                                      (state == READ_DONE && rd_done_fire));

    always_ff @(posedge clk) begin
        if (rst) begin
            state <= IDLE; phase <= INITIALIZE; slot <= 0; half <= 0;
            write_index <= 0; read_count <= 0; command_beats <= 0;
            command_address <= 0; command_tag <= 0; seed_latched <= 0;
            busy <= 0; done <= 0; error <= 0; error_code <= 0;
            first_fail_addr <= 0; expected <= 0; actual <= 0;
            cycles <= 0; read_beats <= 0; write_beats <= 0; watchdog_count <= 0;
            check_pending <= 0; check_shape_error <= 0;
            check_address <= 0; check_expected <= 0; check_actual <= 0;
        end else begin
            check_pending <= 0;
            if (read_fire && !error) begin
                check_pending <= 1;
                check_shape_error <= incoming_shape_error;
                check_address <= read_address;
                check_expected <= expected_word;
                check_actual <= rd_data;
            end
            // Snapshot counters freeze on success or the first fault, including
            // the fault-detecting edge. They exclude subsequent draining work.
            if (busy && !error) begin
                cycles <= cycles + 1'b1;
                if (read_fire) read_beats <= read_beats + 1'b1;
                if (write_fire) write_beats <= write_beats + 1'b1;
                if (any_progress) watchdog_count <= 0;
                else if (watchdog_count < WATCHDOG_LIMIT) watchdog_count <= watchdog_count + 1'b1;
            end
            if (!error && fault_code != 0) begin
                error <= 1; done <= 0; error_code <= fault_code;
                first_fail_addr <= compare_error ? check_address : command_address;
                if (compare_error && fault_code == MISMATCH) begin
                    expected <= check_expected; actual <= check_actual;
                end
            end
            case (state)
                IDLE: if (start && !error) begin
                    done <= 0; cycles <= 0; read_beats <= 0; write_beats <= 0;
                    first_fail_addr <= 0; expected <= 0; actual <= 0;
                    watchdog_count <= 0;
                    if (fault_code == 0) begin
                        busy <= 1; seed_latched <= seed; phase <= INITIALIZE;
                        slot <= 0; half <= 0; command_tag <= 0; state <= PREPARE;
                    end
                end
                PREPARE: if (failed) state <= DRAIN;
                else begin
                    command_address <= slot_address(slot) + (phase == OVERLAY ? 32'd8 : (half ? 32'd128 : 32'd0));
                    command_beats <= phase == OVERLAY ? {1'b0,slot}+5'd1 : 5'd16;
                    write_index <= 0; read_count <= 0;
                    state <= (phase == INITIALIZE || phase == OVERLAY) ? WRITE_CMD : READ_CMD;
                end
                WRITE_CMD: if (wr_command_fire) state <= WRITE_DATA;
                WRITE_DATA: if (wr_done_fire) state <= DRAIN;
                else if (write_fire) begin
                    if ({1'b0,write_index}+5'd1 == command_beats) state <= WRITE_DONE;
                    else write_index <= write_index + 1'b1;
                end
                WRITE_DONE: if (wr_done_fire && failed) state <= DRAIN;
                READ_CMD: if (rd_command_fire) state <= READ_DATA;
                READ_DATA: if (rd_done_fire) state <= DRAIN;
                else if (read_fire) begin
                    if (read_count < 16) read_count <= read_count + 1'b1;
                    if (read_count+5'd1 == command_beats) state <= READ_DONE;
                end
                READ_DONE: if (rd_done_fire && failed) state <= DRAIN;
                DRAIN: if (axi_quiescent) begin busy <= 0; state <= IDLE; end
                default: state <= IDLE;
            endcase
            if (command_success) begin
                command_tag <= command_tag + 1'b1;
                state <= PREPARE;
                if (phase == OVERLAY || half) begin
                    half <= 0;
                    if (slot == 15) begin
                        slot <= 0;
                        if (phase == CHECK_OVERLAY) begin busy <= 0; done <= 1; state <= IDLE; end
                        else case (phase)
                            INITIALIZE: phase <= CHECK_BASE;
                            CHECK_BASE: phase <= OVERLAY;
                            default: phase <= CHECK_OVERLAY;
                        endcase
                    end else slot <= slot + 1'b1;
                end else half <= 1;
            end
        end
    end

`ifndef SYNTHESIS
    initial if (WATCHDOG_LIMIT < 1) $fatal(1,"WATCHDOG_LIMIT must be positive");
    logic hold_rd, hold_wr, hold_data;
    logic [52:0] prior_rd, prior_wr;
    logic [71:0] prior_data;
    always @(posedge clk) begin
        if (rst) begin hold_rd <= 0; hold_wr <= 0; hold_data <= 0; end
        else begin
            // A four-state simulation must not let an unknown DDR word pass
            // the hardware comparator through Verilog's optimistic if rules.
            if (read_fire && ((^rd_data === 1'bx) || (^expected_word === 1'bx)))
                $fatal(1,"unknown diagnostic read data or expected pattern");
            if (hold_rd && (!rd_cmd_valid || {rd_cmd_addr,rd_cmd_beats,rd_cmd_tag} !== prior_rd))
                $fatal(1,"diagnostic read command changed under stall");
            if (hold_wr && (!wr_cmd_valid || {wr_cmd_addr,wr_cmd_beats,wr_cmd_tag} !== prior_wr))
                $fatal(1,"diagnostic write command changed under stall");
            if (hold_data && (!wr_data_valid || {wr_data,wr_data_strb} !== prior_data))
                $fatal(1,"diagnostic write data changed under stall");
            hold_rd <= rd_cmd_valid && !rd_cmd_ready;
            hold_wr <= wr_cmd_valid && !wr_cmd_ready;
            // A rejected terminal completion explicitly cancels the producer.
            hold_data <= wr_data_valid && !wr_data_ready && !wr_done_fire;
            prior_rd <= {rd_cmd_addr,rd_cmd_beats,rd_cmd_tag};
            prior_wr <= {wr_cmd_addr,wr_cmd_beats,wr_cmd_tag};
            prior_data <= {wr_data,wr_data_strb};
        end
    end
`endif
endmodule
