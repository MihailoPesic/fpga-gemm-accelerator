`timescale 1ns/1ps
// Row-to-burst planner. The client owns data movement and acknowledges a
// read only after local delivery, or a write only after its AXI B response.
module gemm_dma_rows #(
    parameter logic [32:0] DDR_BYTES = 33'd134217728,
    parameter integer READ_SLOTS = 1
) (
    input wire clk, rst,
    input wire req_valid,
    output wire req_ready,
    input wire req_write,
    input wire [31:0] req_base, req_stride,
    input wire [5:0] req_rows,
    input wire [8:0] req_row_bytes,
    output wire busy,
    output wire burst_valid,
    input wire burst_ready,
    output wire burst_write,
    output logic [31:0] burst_addr,
    output logic [4:0] burst_beats, burst_row, burst_word,
    output wire burst_row_last, burst_last,
    output wire [7:0] burst_final_strb,
    input wire complete_valid,
    output wire complete_ready,
    input wire [15:0] complete_status,
    // Depth-four reads may cancel an unaccepted offer, but every accepted
    // command still requires its ordered completion before operation DONE.
    input wire cancel,
    input wire [15:0] cancel_status,
    output wire done_valid,
    input wire done_ready,
    output logic [15:0] done_status
);
    localparam logic [15:0] BAD_DESC = 16'd3;
    typedef enum logic [2:0] {
        IDLE, MULTIPLY, FOOTPRINT, VALIDATE, ISSUE, WAIT_COMPLETE, DONE, ACTIVE
    } state_t;
    state_t state;
    logic saved_write;
    logic [31:0] saved_base, saved_stride, row_address;
    logic [5:0] saved_rows, words_per_row;
    logic [8:0] saved_row_bytes;
    logic [5:0] last_row_index;
    logic [9:0] rounded_row_bytes;
    logic [37:0] last_row_offset;
    logic [32:0] first_row_end;
    logic [38:0] touched_end;
    logic [2:0] outstanding;
    logic issued_all, cancelled;

    // 39 bits cover even invalid six-bit row counts and maximum 32-bit strides.
    // Register the product and endpoint separately so the DSP path does not
    // include the footprint addition, bounds comparison, and burst enables.
    wire invalid_request = saved_rows == 0 || saved_rows > 32 ||
        saved_row_bytes == 0 || saved_row_bytes > 256 ||
        saved_base[2:0] != 0 || saved_stride[2:0] != 0 ||
        saved_stride < {22'd0,rounded_row_bytes} ||
        (saved_write && saved_row_bytes[1:0] != 0) ||
        touched_end > {6'd0,DDR_BYTES};

    function automatic [4:0] choose_beats(input logic [31:0] address,
                                         input logic [5:0] remaining);
        logic [12:0] bytes_to_boundary;
        logic [9:0] words_to_boundary;
        logic [5:0] count;
        begin
            bytes_to_boundary = 13'd4096 - {1'b0,address[11:0]};
            words_to_boundary = bytes_to_boundary[12:3];
            count = remaining > 6'd16 ? 6'd16 : remaining;
            if (words_to_boundary < {4'd0,count}) count = words_to_boundary[5:0];
            choose_beats = count[4:0];
        end
    endfunction

    wire [5:0] next_word = {1'b0,burst_word} + {1'b0,burst_beats};
    wire [31:0] next_burst_address = burst_addr + {24'd0,burst_beats,3'd0};
    wire [31:0] next_row_address = row_address + saved_stride;
    assign req_ready = state == IDLE && !rst;
    assign busy = state != IDLE;
    wire queued_read = READ_SLOTS == 4 && !saved_write;
    wire completion_fire = complete_valid && complete_ready;
    wire cancel_read = queued_read && cancel;
    wire stop_issue = cancelled || cancel_read ||
                      (completion_fire && complete_status != 0);
    assign burst_valid = (state == ISSUE || (state == ACTIVE &&
                         !issued_all && outstanding < 4 && !stop_issue)) && !rst;
    assign burst_write = saved_write;
    assign burst_row_last = next_word == words_per_row;
    assign burst_last = burst_row_last && {1'b0,burst_row}+6'd1 == saved_rows;
    assign burst_final_strb = saved_write && burst_row_last && saved_row_bytes[2] ?
                              8'h0f : 8'hff;
    assign complete_ready = (state == WAIT_COMPLETE ||
                            (state == ACTIVE && outstanding != 0)) && !rst;
    assign done_valid = state == DONE && !rst;
    wire issue_fire = burst_valid && burst_ready;
    wire [3:0] remaining = {1'b0,outstanding} + {3'd0,issue_fire} -
                           {3'd0,completion_fire};

    always_ff @(posedge clk) begin
        if (rst) begin
            state <= IDLE;
            saved_write <= 0; saved_base <= 0; saved_stride <= 0;
            saved_rows <= 0; saved_row_bytes <= 0; words_per_row <= 0;
            last_row_index <= 0; rounded_row_bytes <= 0;
            last_row_offset <= 0; first_row_end <= 0; touched_end <= 0;
            row_address <= 0; burst_addr <= 0;
            burst_beats <= 0; burst_row <= 0; burst_word <= 0;
            done_status <= 0;
            outstanding <= 0; issued_all <= 0; cancelled <= 0;
        end else begin
            if (queued_read && state != IDLE && state != DONE &&
                (cancel_read || (completion_fire && complete_status != 0))) begin
                cancelled <= 1;
                if (done_status == 0)
                    done_status <= cancel_read ? (cancel_status != 0 ? cancel_status : 16'd8) : complete_status;
            end
            case (state)
                IDLE: if (req_valid) begin
                    saved_write <= req_write;
                    saved_base <= req_base; saved_stride <= req_stride;
                    saved_rows <= req_rows; saved_row_bytes <= req_row_bytes;
                    last_row_index <= req_rows - 6'd1;
                    rounded_row_bytes <= ({1'b0,req_row_bytes} + 10'd7) & 10'h3f8;
                    done_status <= 0;
                    outstanding <= 0; issued_all <= 0; cancelled <= 0;
                    state <= MULTIPLY;
                end
                MULTIPLY: begin
                    // A 32-bit stride times a six-bit index fits in 38 bits.
                    last_row_offset <= {6'd0,saved_stride} * {32'd0,last_row_index};
                    first_row_end <= {1'b0,saved_base} + {23'd0,rounded_row_bytes};
                    state <= FOOTPRINT;
                end
                FOOTPRINT: begin
                    touched_end <= {1'b0,last_row_offset} + {6'd0,first_row_end};
                    state <= VALIDATE;
                end
                VALIDATE: if (invalid_request) begin
                    done_status <= BAD_DESC;
                    state <= DONE;
                end else begin
                    row_address <= saved_base; burst_addr <= saved_base;
                    words_per_row <= rounded_row_bytes[8:3];
                    burst_beats <= choose_beats(saved_base,rounded_row_bytes[8:3]);
                    burst_row <= 0; burst_word <= 0;
                    state <= queued_read ? ACTIVE : ISSUE;
                end
                ISSUE: if (burst_ready) state <= WAIT_COMPLETE;
                WAIT_COMPLETE: if (complete_valid) begin
                    if (complete_status != 0 || burst_last) begin
                        done_status <= complete_status;
                        state <= DONE;
                    end else begin
                        if (burst_row_last) begin
                            row_address <= next_row_address;
                            burst_addr <= next_row_address;
                            burst_beats <= choose_beats(next_row_address,words_per_row);
                            burst_row <= burst_row + 1'b1;
                            burst_word <= 0;
                        end else begin
                            burst_addr <= next_burst_address;
                            burst_beats <= choose_beats(next_burst_address,words_per_row-next_word);
                            burst_word <= next_word[4:0];
                        end
                        state <= ISSUE;
                    end
                end
                ACTIVE: begin
                    outstanding <= remaining[2:0];
                    if (issue_fire) begin
                        if (burst_last) issued_all <= 1;
                        else if (burst_row_last) begin
                            row_address <= next_row_address;
                            burst_addr <= next_row_address;
                            burst_beats <= choose_beats(next_row_address,words_per_row);
                            burst_row <= burst_row + 1'b1;
                            burst_word <= 0;
                        end else begin
                            burst_addr <= next_burst_address;
                            burst_beats <= choose_beats(next_burst_address,words_per_row-next_word);
                            burst_word <= next_word[4:0];
                        end
                    end
                    if ((issued_all || stop_issue || (issue_fire && burst_last)) && remaining == 0)
                        state <= DONE;
                end
                DONE: if (done_ready) state <= IDLE;
                default: state <= IDLE;
            endcase
        end
    end

`ifndef SYNTHESIS
    initial if (DDR_BYTES == 0 || DDR_BYTES > 33'h100000000)
        $fatal(1,"DDR_BYTES must be in 1..2^32");
    initial if (READ_SLOTS != 1 && READ_SLOTS != 4)
        $fatal(1,"READ_SLOTS must be 1 or 4");
    wire [57:0] burst_payload = {burst_write,burst_addr,burst_beats,burst_row,
                                burst_word,burst_row_last,burst_last,burst_final_strb};
    logic held_burst, held_done;
    logic [57:0] previous_burst;
    logic [15:0] previous_status;
    always @(posedge clk) begin
        if (rst) begin held_burst <= 0; held_done <= 0; end
        else begin
            if (held_burst && !(queued_read && stop_issue) &&
                (!burst_valid || burst_payload !== previous_burst))
                $fatal(1,"row burst changed while stalled");
            if (held_done && (!done_valid || done_status !== previous_status))
                $fatal(1,"row completion changed while stalled");
            if (state == ACTIVE && (outstanding > 4 || remaining > 4))
                $fatal(1,"row read credit overflow/underflow");
            if (burst_valid) begin
                if (burst_beats < 1 || burst_beats > 16 || burst_addr[2:0] != 0 ||
                    {1'b0,burst_addr[11:0]}+{5'd0,burst_beats,3'd0} > 13'd4096 ||
                    next_word > words_per_row ||
                    {1'b0,burst_addr}+{25'd0,burst_beats,3'd0} > DDR_BYTES)
                    $fatal(1,"row burst exceeds its row, page or DDR boundary");
            end
            held_burst <= burst_valid && !burst_ready;
            held_done <= done_valid && !done_ready;
            previous_burst <= burst_payload;
            previous_status <= done_status;
        end
    end
`endif
endmodule
