`timescale 1ns/1ps
// Local-matrix engine: 1<=M,N<=T, 1<=K<=256. See docs/tile-engine.md.
// No DDR, descriptors or transport framing here. Loaded inputs must be ready.
module gemm_tile_engine #(
    parameter integer P = 4,
    parameter integer T = 32,
    parameter integer Q_W = $clog2(T),
    parameter integer DIM_W = $clog2(T+1),
    parameter integer COUNT_W = $clog2(P+1),
    parameter integer ROW_W = $clog2(P),
    parameter integer GROUP_W = (T/P > 1) ? $clog2(T/P) : 1,
    parameter integer CONCURRENT_PORTS = 0
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
    input wire input_buf, output_buf,
    input wire [DIM_W-1:0] m, n,
    input wire [8:0] k,
    output wire busy,
    output logic done, cmd_error,
    output logic [1:0] result_valid,
    output logic [63:0] job_cycles, compute_cycles,
    output logic [31:0] microtiles,
    input wire read_valid,
    output wire read_ready,
    input wire read_buf,
    input wire [Q_W-1:0] read_row,
    input wire [Q_W-2:0] read_pair,
    output logic response_valid,
    input wire response_ready,
    output logic [63:0] response_data,
    output logic [7:0] response_strb,
    output logic response_error
);
    typedef enum logic [1:0] {IDLE, LAUNCH_TILE, RUN_TILE} state_t;
    state_t state;
    logic input_q, output_q;
    logic [DIM_W-1:0] m_q, n_q;
    logic [8:0] k_q;
    logic [GROUP_W-1:0] row_group, col_group, last_row_group, last_col_group;
    logic [1:0] prefetch_left;
    logic [DIM_W-1:0] result_rows [0:1], result_cols [0:1];
    logic read_pending, read_error_q, read_upper_q, read_buf_q;
    wire [63:0] bank_read_data;
    wire memory_load_ready, micro_ready, drain_valid;
    wire [ROW_W-1:0] drain_row;
    wire [P-1:0] drain_mask;
    wire [P*32-1:0] drain_data;

    assign busy = state != IDLE;
    // A completed-buffer read owns its buffer until the response is consumed.
    // Its stored data may outlive a new job using the other result buffer.
    wire read_owned = read_pending || response_valid;
    assign start_ready = !rst && !busy &&
        (CONCURRENT_PORTS ? (!read_owned || output_buf != read_buf_q) : !read_owned);
    wire legal = m >= 1 && m <= T && n >= 1 && n <= T && k >= 1 && k <= 256;
    wire accept_start = start && start_ready && legal;
    // Serial preview: a START pulse wins over host load/read requests.
    wire host_idle = !rst && !busy && !start;
    // Reserve the selected input/result buffers for the complete local job,
    // including LAUNCH_TILE gaps when the inner microtile is briefly idle.
    wire load_allowed = CONCURRENT_PORTS ?
        !rst && (!busy || load_buf != input_q) &&
            !(accept_start && load_buf == input_buf) : host_idle;
    wire read_allowed = CONCURRENT_PORTS ?
        !rst &&
            !(accept_start && read_buf == output_buf) : host_idle;
    assign load_ready = load_allowed && memory_load_ready;
    assign read_ready = read_allowed && !read_owned;
    wire read_fire = read_valid && read_ready;
    wire [Q_W:0] read_col = {1'b0, read_pair, 1'b0};
    wire read_legal = result_valid[read_buf] && read_row < result_rows[read_buf] &&
                      read_col < result_cols[read_buf];

    wire [DIM_W-1:0] remaining_rows = m_q - DIM_W'(row_group*P);
    wire [DIM_W-1:0] remaining_cols = n_q - DIM_W'(col_group*P);
    wire [COUNT_W-1:0] tile_rows = remaining_rows >= P ? COUNT_W'(P) : COUNT_W'(remaining_rows);
    wire [COUNT_W-1:0] tile_cols = remaining_cols >= P ? COUNT_W'(P) : COUNT_W'(remaining_cols);
    wire tile_start = state == LAUNCH_TILE && !rst;
    wire result_write = state == RUN_TILE && drain_valid && !rst;
    wire [Q_W-1:0] result_write_row = Q_W'(row_group*P + drain_row);
    wire tile_final = result_write && drain_row == ROW_W'(P-1);

    gemm_bram_microtile #(.P(P), .T(T)) compute (
        .clk(clk), .rst(rst), .load_valid(load_valid && load_allowed),
        .load_ready(memory_load_ready), .load_bt(load_bt), .load_buf(load_buf),
        .load_q(load_q), .load_word(load_word), .load_data(load_data),
        .start(tile_start), .start_ready(micro_ready), .buffer_id(input_q),
        .a_group(row_group), .bt_group(col_group), .k(k_q), .rows(tile_rows), .cols(tile_cols),
        .busy(), .done(), .cmd_error(), .drain_valid(drain_valid),
        .drain_row(drain_row), .drain_mask(drain_mask), .drain_data(drain_data)
    );
    gemm_result_banks #(.P(P), .T(T)) results (
        .clk(clk), .wr_en(result_write), .wr_buf(output_q),
        .wr_row(result_write_row), .wr_group(col_group), .wr_mask(drain_mask), .wr_data(drain_data),
        .rd_en(read_fire && read_legal), .rd_buf(read_buf), .rd_row(read_row), .rd_pair(read_pair),
        .rd_data(bank_read_data)
    );

    always_ff @(posedge clk) begin
        cmd_error <= 1'b0;
        if (rst) begin
            state <= IDLE;
            done <= 1'b0;
            input_q <= 1'b0;
            output_q <= 1'b0;
            m_q <= '0;
            n_q <= '0;
            k_q <= '0;
            row_group <= '0;
            col_group <= '0;
            last_row_group <= '0;
            last_col_group <= '0;
            prefetch_left <= '0;
            result_valid <= '0;
            result_rows[0] <= '0;
            result_rows[1] <= '0;
            result_cols[0] <= '0;
            result_cols[1] <= '0;
            job_cycles <= '0;
            compute_cycles <= '0;
            microtiles <= '0;
            read_pending <= 1'b0;
            read_error_q <= 1'b0;
            read_upper_q <= 1'b0;
            read_buf_q <= 1'b0;
            response_valid <= 1'b0;
            response_data <= '0;
            response_strb <= '0;
            response_error <= 1'b0;
        end else begin
            if (start && (!start_ready || !legal)) cmd_error <= 1'b1;
            if (response_valid && response_ready) response_valid <= 1'b0;
            read_pending <= read_fire;
            if (read_fire) begin
                read_buf_q <= read_buf;
                read_error_q <= !read_legal;
                read_upper_q <= read_col + 1 < result_cols[read_buf];
            end
            if (read_pending) begin
                response_valid <= 1'b1;
                response_error <= read_error_q;
                response_strb <= read_error_q ? 8'h00 : read_upper_q ? 8'hff : 8'h0f;
                response_data <= read_error_q ? 64'd0 :
                    {read_upper_q ? bank_read_data[63:32] : 32'd0, bank_read_data[31:0]};
            end
            if (busy) job_cycles <= job_cycles + 1'b1;
            case (state)
                IDLE: if (accept_start) begin
                    state <= LAUNCH_TILE;
                    done <= 1'b0;
                    input_q <= input_buf;
                    output_q <= output_buf;
                    m_q <= m;
                    n_q <= n;
                    k_q <= k;
                    row_group <= '0;
                    col_group <= '0;
                    last_row_group <= GROUP_W'((m-1)/P);
                    last_col_group <= GROUP_W'((n-1)/P);
                    result_valid[output_buf] <= 1'b0;
                    result_rows[output_buf] <= m;
                    result_cols[output_buf] <= n;
                    job_cycles <= '0;
                    compute_cycles <= '0;
                    microtiles <= '0;
                end
                LAUNCH_TILE: if (micro_ready) begin
                    state <= RUN_TILE;
                    prefetch_left <= 2'd3;
                    microtiles <= microtiles + 1'b1;
                end
                RUN_TILE: begin
                    if (prefetch_left != 0) prefetch_left <= prefetch_left - 1'b1;
                    else compute_cycles <= compute_cycles + 1'b1;
                    if (tile_final) begin
                        if (col_group != last_col_group) begin
                            col_group <= col_group + 1'b1;
                            state <= LAUNCH_TILE;
                        end else if (row_group != last_row_group) begin
                            row_group <= row_group + 1'b1;
                            col_group <= '0;
                            state <= LAUNCH_TILE;
                        end else begin
                            state <= IDLE;
                            done <= 1'b1;
                            result_valid[output_q] <= 1'b1;
                        end
                    end
                end
                default: state <= IDLE;
            endcase
        end
    end
`ifndef SYNTHESIS
    initial begin
        if (CONCURRENT_PORTS != 0 && CONCURRENT_PORTS != 1)
            $fatal(1, "CONCURRENT_PORTS must be 0 or 1");
    end
`endif
endmodule
