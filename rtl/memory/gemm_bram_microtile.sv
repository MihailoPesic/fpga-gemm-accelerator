`timescale 1ns/1ps
// See docs/memory.md. The caller loads valid rows and reserves result space.
// Host/DMA writes to the computing input buffer are blocked until completion.
module gemm_bram_microtile #(
    parameter integer P = 4,
    parameter integer T = 32,
    parameter integer COUNT_W = $clog2(P+1),
    parameter integer ROW_W = $clog2(P),
    parameter integer Q_W = $clog2(T),
    parameter integer GROUP_W = (T/P > 1) ? $clog2(T/P) : 1
) (
    input wire clk, rst,
    input wire load_valid,
    output wire load_ready,
    input wire load_bt, load_buf,
    input wire [Q_W-1:0] load_q,
    input wire [4:0] load_word,
    input wire [63:0] load_data,
    input wire start,
    output wire start_ready,
    input wire buffer_id,
    input wire [GROUP_W-1:0] a_group, bt_group,
    input wire [8:0] k,
    input wire [COUNT_W-1:0] rows, cols,
    output wire busy, done,
    output logic cmd_error,
    output wire drain_valid,
    output wire [ROW_W-1:0] drain_row,
    output wire [P-1:0] drain_mask,
    output wire [P*32-1:0] drain_data
);
    typedef enum logic [2:0] {IDLE, READ_FIRST, READ_SECOND, LAUNCH, RUN} state_t;
    state_t state;
    logic buf_q;
    logic [GROUP_W-1:0] a_group_q, bt_group_q;
    logic [8:0] k_q;
    logic [COUNT_W-1:0] rows_q, cols_q;
    logic [5:0] word_count;
    logic [P*64-1:0] a_current, bt_current, a_next, bt_next;
    logic rd_pending;
    wire [P*64-1:0] a_words, bt_words;
    wire feed_valid, core_done;
    wire [7:0] feed_index;
    wire [P*8-1:0] a_vector, bt_vector;

    assign busy = state != IDLE;
    assign start_ready = !rst && !busy;
    wire legal = k >= 1 && k <= 256 && rows >= 1 && rows <= P &&
                 cols >= 1 && cols <= P && a_group < T/P && bt_group < T/P;
    wire accept_start = start && start_ready && legal;
    // A launch takes priority over a same-buffer write at its acceptance edge.
    assign load_ready = !rst && (!busy || load_buf != buf_q) &&
                        !(accept_start && load_buf == buffer_id);

    // Read word n+2 while consuming byte 6 of word n. On byte 7, capture
    // the BRAM response into next and promote the OLD next into current.
    wire [5:0] future_word = {1'b0, feed_index[7:3]} + 6'd2;
    wire fetch_future = state == RUN && feed_valid && feed_index[2:0] == 3'd6 &&
                        future_word < word_count;
    wire rd_en = !rst && (state == READ_FIRST ||
                          (state == READ_SECOND && k_q > 8) || fetch_future);
    wire [4:0] rd_word = state == READ_FIRST ? 5'd0 :
                         state == READ_SECOND ? 5'd1 : future_word[4:0];

    gemm_operand_banks #(.P(P), .T(T)) operands (
        .clk(clk), .wr_en(load_valid && load_ready), .wr_bt(load_bt),
        .wr_buf(load_buf), .wr_q(load_q), .wr_word(load_word), .wr_data(load_data),
        .rd_en(rd_en), .rd_buf(buf_q), .rd_a_group(a_group_q),
        .rd_bt_group(bt_group_q), .rd_word(rd_word),
        .a_words(a_words), .bt_words(bt_words)
    );

    for (genvar lane=0; lane<P; lane=lane+1) begin : unpack_lanes
        assign a_vector[lane*8 +: 8] =
            lane < rows_q ? a_current[lane*64 + feed_index[2:0]*8 +: 8] : 8'd0;
        assign bt_vector[lane*8 +: 8] =
            lane < cols_q ? bt_current[lane*64 + feed_index[2:0]*8 +: 8] : 8'd0;
    end

    gemm_microtile #(.P(P)) core (
        .clk(clk), .rst(rst), .start(state == LAUNCH && !rst),
        .k(k_q), .rows(rows_q), .cols(cols_q), .start_ready(), .busy(),
        .done(core_done), .cmd_error(), .feed_valid(feed_valid), .feed_index(feed_index),
        .a_vector(a_vector), .bt_vector(bt_vector),
        .drain_valid(drain_valid), .drain_row(drain_row),
        .drain_mask(drain_mask), .drain_data(drain_data)
    );
    assign done = core_done && !rst;

    always_ff @(posedge clk) begin
        cmd_error <= 1'b0;
        rd_pending <= rd_en;
        if (rst) begin
            state <= IDLE;
            buf_q <= 1'b0;
            a_group_q <= '0;
            bt_group_q <= '0;
            k_q <= '0;
            rows_q <= '0;
            cols_q <= '0;
            word_count <= '0;
            rd_pending <= 1'b0;
        end else begin
            if (start && (!start_ready || !legal)) cmd_error <= 1'b1;
            case (state)
                IDLE: if (accept_start) begin
                    buf_q <= buffer_id;
                    a_group_q <= a_group;
                    bt_group_q <= bt_group;
                    k_q <= k;
                    rows_q <= rows;
                    cols_q <= cols;
                    word_count <= 6'((k + 9'd7) >> 3);
                    state <= READ_FIRST;
                end
                READ_FIRST: state <= READ_SECOND;
                READ_SECOND: begin
                    a_current <= a_words;
                    bt_current <= bt_words;
                    state <= LAUNCH;
                end
                LAUNCH: begin
                    if (k_q > 8) begin
                        a_next <= a_words;
                        bt_next <= bt_words;
                    end
                    state <= RUN;
                end
                RUN: begin
                    if (rd_pending) begin
                        a_next <= a_words;
                        bt_next <= bt_words;
                    end
                    if (feed_valid && feed_index[2:0] == 3'd7 &&
                        {1'b0, feed_index} + 9'd1 < k_q) begin
                        a_current <= a_next;
                        bt_current <= bt_next;
                    end
                    if (drain_valid && drain_row == ROW_W'(P-1)) state <= IDLE;
                end
                default: state <= IDLE;
            endcase
        end
    end
`ifndef SYNTHESIS
    initial begin
        if ((P != 4 && P != 8) || (T != 8 && T != 32) || T % P != 0)
            $fatal(1, "Supported geometry: P=4/8, T=8/32, T divisible by P");
    end
`endif
endmodule
