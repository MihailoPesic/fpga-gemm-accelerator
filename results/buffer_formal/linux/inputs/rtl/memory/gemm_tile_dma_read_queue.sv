`timescale 1ns/1ps
// Four ordered read descriptors share one held operand-bank output word.
// The external burst engine reserves and validates the complete data buffers.
module gemm_tile_dma_read_queue #(
    parameter integer T=32, Q_W=$clog2(T)
) (
    input wire clk, rst,
    input wire stop,
    input wire [15:0] stop_status,
    input wire saved_bt, saved_buf,
    input wire plan_valid,
    output wire plan_ready,
    input wire [31:0] plan_addr,
    input wire [4:0] plan_beats, plan_row, plan_word,
    input wire plan_last,
    output wire complete_valid,
    input wire complete_ready,
    output logic [15:0] complete_status,
    output logic [15:0] fault_code,
    output wire load_valid,
    input wire load_ready,
    output wire load_bt, load_buf,
    output logic [Q_W-1:0] load_q,
    output logic [4:0] load_word,
    output logic [63:0] load_data,
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
    input wire [15:0] rd_done_status, rd_done_tag
);
    localparam logic [15:0] PROTOCOL=16'd8;
    logic [4:0] rows [0:3], words [0:3], beats [0:3];
    logic final_burst [0:3];
    logic [1:0] head, tail;
    logic [2:0] owned;
    logic [4:0] received, delivered;
    logic load_pending, completion_pending;

    // The same edge reserves metadata and accepts the external local command.
    // Free credit exists before VALID; no returned word can precede that edge.
    assign rd_cmd_valid = plan_valid && owned < 4 && !stop && fault_code == 0 && !rst;
    assign plan_ready = rd_cmd_ready && owned < 4 && !stop && fault_code == 0 && !rst;
    assign rd_cmd_addr = plan_addr;
    assign rd_cmd_beats = plan_beats;
    assign rd_cmd_tag = {14'd0,tail};
    wire push = rd_cmd_valid && rd_cmd_ready;
    assign complete_valid = completion_pending && !rst;
    wire pop = complete_valid && complete_ready;

    assign load_valid = load_pending && !rst;
    assign load_bt = saved_bt;
    assign load_buf = saved_buf;
    wire load_fire = load_valid && load_ready;
    assign rd_data_ready = owned != 0 && !completion_pending &&
                           (!load_pending || load_ready) && !rst;
    assign rd_done_ready = owned != 0 && !completion_pending &&
                           !load_pending && !rd_data_valid && !rst;
    wire data_fire = rd_data_valid && rd_data_ready;
    wire done_fire = rd_done_valid && rd_done_ready;
    wire [5:0] operand_word = {1'b0,words[head]} + {2'd0,rd_data_index};
    wire bad_word = data_fire && (rd_data_tag != {14'd0,head} ||
        received >= beats[head] || rd_data_index != received[3:0] ||
        rd_data_last != (received+5'd1 == beats[head]) ||
        operand_word >= 32 || rows[head] >= T);
    always_comb begin
        fault_code = 0;
        if (bad_word || (done_fire && (rd_done_tag != {14'd0,head} ||
            (rd_done_status == 0 && (received != beats[head] || delivered != beats[head])))))
            fault_code = PROTOCOL;
        else if (done_fire && rd_done_status != 0)
            fault_code = rd_done_status == 7 ? 16'd7 : PROTOCOL;
    end

    integer slot;
    always_ff @(posedge clk) begin
        if (rst) begin
            head <= 0; tail <= 0; owned <= 0;
            received <= 0; delivered <= 0;
            load_pending <= 0; completion_pending <= 0; complete_status <= 0;
            load_q <= 0; load_word <= 0; load_data <= 0;
            for (slot=0; slot<4; slot=slot+1) begin
                rows[slot] <= 0; words[slot] <= 0; beats[slot] <= 0;
                final_burst[slot] <= 0;
            end
        end else begin
            case ({push,pop})
                2'b10: owned <= owned + 1'b1;
                2'b01: owned <= owned - 1'b1;
                default: begin end
            endcase
            if (push) begin
                rows[tail] <= plan_row; words[tail] <= plan_word;
                beats[tail] <= plan_beats; final_burst[tail] <= plan_last;
                tail <= tail + 1'b1;
            end
            if (load_fire) begin
                load_pending <= 0;
                delivered <= delivered + 1'b1;
            end
            if (data_fire) begin
                if (received != 31) received <= received + 1'b1;
                // Preserve an older stalled offer. After a fault, all later
                // validated words drain without creating another bank write.
                if (!stop && fault_code == 0) begin
                    load_pending <= 1; load_data <= rd_data;
                    load_q <= rows[head][Q_W-1:0]; load_word <= operand_word[4:0];
                end
            end
            if (done_fire) begin
                completion_pending <= 1;
                complete_status <= stop ? (stop_status != 0 ? stop_status : PROTOCOL) : fault_code;
            end
            if (pop) begin
                head <= head + 1'b1;
                completion_pending <= 0;
                received <= 0; delivered <= 0;
            end
        end
    end

`ifndef SYNTHESIS
    initial if ((T != 8 && T != 32) || Q_W != $clog2(T))
        $fatal(1,"Unsupported queued read geometry");
    logic held_complete;
    logic [15:0] previous_status;
    always @(posedge clk) begin
        if (rst) held_complete <= 0;
        else begin
            if (owned > 4 || (pop && owned == 0))
                $fatal(1,"DMA read metadata credit overflow/underflow");
            if (completion_pending && load_pending)
                $fatal(1,"DMA read completed before its bank offer drained");
            if (pop && final_burst[head] && owned != 1)
                $fatal(1,"DMA queued work follows the final read descriptor");
            if (held_complete && (!complete_valid || complete_status !== previous_status))
                $fatal(1,"DMA read terminal changed while stalled");
            held_complete <= complete_valid && !complete_ready;
            previous_status <= complete_status;
        end
    end
`endif
endmodule
