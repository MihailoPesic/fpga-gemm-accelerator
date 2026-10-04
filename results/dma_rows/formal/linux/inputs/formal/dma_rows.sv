// Bounded command-interface checks for four 20-byte rows, stride64, base0xff0.
// Five bursts exercise a 4KiB split and the odd INT32 output-tail strobe.
module dma_rows_formal #(
    parameter integer READ_SLOTS = 1
) (
    input wire clk, rst, req_valid, req_write, burst_ready,
    input wire complete_valid, cancel, done_ready,
    input wire [15:0] complete_status, cancel_status,
    output wire req_ready, busy, burst_valid, burst_write,
    output wire [31:0] burst_addr,
    output wire [4:0] burst_beats, burst_row, burst_word,
    output wire burst_row_last, burst_last,
    output wire [7:0] burst_final_strb,
    output wire complete_ready, done_valid,
    output wire [15:0] done_status,
    output reg [3:0] pending,
    output reg cover_read_done, cover_write_done,
    output reg cover_credit_four, cover_simultaneous,
    output reg cover_cancelled_drain
);
    gemm_dma_rows #(.READ_SLOTS(READ_SLOTS)) dut (
        .clk(clk), .rst(rst), .req_valid(req_valid), .req_ready(req_ready),
        .req_write(req_write), .req_base(32'h00000ff0), .req_stride(32'd64),
        .req_rows(6'd4), .req_row_bytes(9'd20), .busy(busy),
        .burst_valid(burst_valid), .burst_ready(burst_ready),
        .burst_write(burst_write), .burst_addr(burst_addr),
        .burst_beats(burst_beats), .burst_row(burst_row), .burst_word(burst_word),
        .burst_row_last(burst_row_last), .burst_last(burst_last),
        .burst_final_strb(burst_final_strb), .complete_valid(complete_valid),
        .complete_ready(complete_ready), .complete_status(complete_status),
        .cancel(cancel), .cancel_status(cancel_status),
        .done_valid(done_valid), .done_ready(done_ready), .done_status(done_status)
    );
    wire issue = burst_valid && burst_ready;
    wire complete = complete_valid && complete_ready;
    wire [57:0] payload = {burst_write, burst_addr, burst_beats, burst_row,
                          burst_word, burst_row_last, burst_last, burst_final_strb};
    wire withdraw = READ_SLOTS == 4 && !burst_write &&
                    (cancel || (complete && complete_status != 0));
    reg past_valid, ghost_write, stalled_burst, stalled_done, saw_cancel, saw_any_cancel;
    reg [3:0] issued;
    reg [31:0] expected_addr;
    reg [4:0] expected_beats, expected_row, expected_word;
    always @* begin
        expected_addr = 0;
        expected_beats = 0;
        expected_row = 0;
        expected_word = 0;
        case (issued)
            0: begin expected_addr = 32'hff0;  expected_beats = 2; end
            1: begin expected_addr = 32'h1000; expected_beats = 1; expected_word = 2; end
            2: begin expected_addr = 32'h1030; expected_beats = 3; expected_row = 1; end
            3: begin expected_addr = 32'h1070; expected_beats = 3; expected_row = 2; end
            4: begin expected_addr = 32'h10b0; expected_beats = 3; expected_row = 3; end
        endcase
    end
    initial begin
        past_valid = 0; pending = 0; issued = 0; ghost_write = 0;
        stalled_burst = 0; stalled_done = 0; saw_cancel = 0; saw_any_cancel = 0;
        cover_read_done = 0; cover_write_done = 0; cover_credit_four = 0;
        cover_simultaneous = 0; cover_cancelled_drain = 0;
    end
    always @(posedge clk) begin
        if (rst) begin
            past_valid <= 0; pending <= 0; issued <= 0; ghost_write <= 0;
            stalled_burst <= 0; stalled_done <= 0; saw_cancel <= 0; saw_any_cancel <= 0;
            cover_read_done <= 0; cover_write_done <= 0; cover_credit_four <= 0;
            cover_simultaneous <= 0; cover_cancelled_drain <= 0;
        end else begin
            past_valid <= 1;
            if (past_valid) begin
                // Only unaccepted queued-read offers may withdraw on cancel/error.
                if ($past(burst_valid && !burst_ready) && !withdraw) begin
                    assert(burst_valid);
                    assert(payload == $past(payload));
                end
                if ($past(done_valid && !done_ready)) begin
                    assert(done_valid);
                    assert(done_status == $past(done_status));
                end
            end
            assert(pending <= (ghost_write ? 1 : READ_SLOTS));
            if (complete_ready) assert(pending != 0);
            if (done_valid) assert(pending == 0);
            if (burst_valid) begin
                assert(issued < 5);
                assert(burst_write == ghost_write);
                assert(burst_addr == expected_addr);
                assert(burst_beats == expected_beats);
                assert(burst_row == expected_row);
                assert(burst_word == expected_word);
                assert(burst_row_last == (issued != 0));
                assert(burst_last == (issued == 4));
                assert(burst_final_strb == ((ghost_write && issued != 0) ? 8'h0f : 8'hff));
                assert(burst_addr[2:0] == 0);
                assert({1'b0, burst_addr[11:0]} + {5'd0, burst_beats, 3'd0} <= 4096);
            end
            if (req_valid && req_ready) begin
                pending <= 0; issued <= 0; ghost_write <= req_write;
                stalled_burst <= 0; stalled_done <= 0; saw_cancel <= 0; saw_any_cancel <= 0;
            end else begin
                pending <= pending + issue - complete;
                if (issue) issued <= issued + 1;
                if (burst_valid && !burst_ready) stalled_burst <= 1;
                if (done_valid && !done_ready) stalled_done <= 1;
                if (busy && cancel) saw_any_cancel <= 1;
                // Detection leaves accepted traffic for a later completion edge.
                if (READ_SLOTS == 4 && !ghost_write && busy && cancel &&
                        cancel_status != 0 && pending > (complete ? 1 : 0))
                    saw_cancel <= 1;
            end
            if (pending == 4 && !ghost_write) cover_credit_four <= 1;
            if (issue && complete) cover_simultaneous <= 1;
            if (done_valid && done_ready && done_status == 0 && issued == 5 &&
                    !saw_any_cancel && !cancel && stalled_burst && stalled_done) begin
                if (ghost_write) cover_write_done <= 1;
                else cover_read_done <= 1;
            end
            if (done_valid && done_ready && done_status != 0 && saw_cancel && pending == 0)
                cover_cancelled_drain <= 1;
        end
    end
endmodule
