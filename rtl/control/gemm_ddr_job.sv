`timescale 1ns/1ps
// Serial DDR GEMM job controller. Validation precedes the accepted-job epoch;
// no DMA or compute request is made for an invalid descriptor. The surrounding
// platform must coordinate reset across this controller, DMA, AXI and MIG.
module gemm_ddr_job #(
    parameter integer P=4, T=32, DIM_W=$clog2(T+1),
    parameter logic [32:0] DDR_BYTES=33'd134217728
) (
    input wire clk, rst, ddr_ready, host_busy,
    input wire start_valid,
    output wire start_ready,
    input wire [31:0] cfg_job_id, cfg_m, cfg_n, cfg_k,
    input wire [31:0] cfg_a_base, cfg_bt_base, cfg_c_base,
    input wire [31:0] cfg_a_stride, cfg_bt_stride, cfg_c_stride,
    input wire [31:0] cfg_mode, cfg_watchdog,
    output logic start_rsp_valid,
    input wire start_rsp_ready,
    output logic [15:0] start_rsp_status,
    output logic job_accepted,
    input wire clear_status,
    output wire [15:0] clear_status_code,
    output wire ready, busy,
    output logic done, error, reset_required,
    output logic [15:0] error_code,
    output logic [31:0] last_job_id,
    output logic [63:0] job_cycles, compute_cycles, read_beats, write_beats,
    output logic [63:0] write_valid_bytes, input_wait_cycles, read_stall_cycles, write_stall_cycles,
    output wire dma_req_valid,
    input wire dma_req_ready,
    output wire dma_req_write, dma_req_bt, dma_req_buf,
    output logic [31:0] dma_req_base, dma_req_stride,
    output wire [5:0] dma_req_rows,
    output wire [8:0] dma_req_row_bytes,
    input wire dma_busy, dma_done_valid,
    input wire [15:0] dma_done_status,
    output wire dma_done_ready,
    input wire dma_fatal,
    input wire [15:0] dma_fatal_code,
    output wire compute_start,
    input wire compute_ready, compute_busy, compute_done, compute_error, compute_progress,
    input wire [63:0] compute_cycles_in,
    output wire compute_input_buf, compute_output_buf,
    output wire [DIM_W-1:0] compute_m, compute_n,
    output wire [8:0] compute_k,
    input wire axi_rvalid, axi_rready, axi_wvalid, axi_wready,
    input wire [7:0] axi_wstrb,
    input wire axi_bvalid, axi_bready,
    input wire [1:0] axi_bresp,
    input wire axi_quiescent, mem_progress
);
    localparam logic [15:0] BAD_DESC=3, BUSY_CODE=4, NOT_READY=5,
                            MEM_RESP=7, PROTOCOL=8, WATCHDOG=9, CALIB_LOST=10;
    typedef enum logic [2:0] {V_IDLE, V_BASIC, V_PRODUCT, V_ADD, V_CHECK} validation_t;
    typedef enum logic [3:0] {J_IDLE, A_REQ, A_WAIT, BT_REQ, BT_WAIT, CORE_REQ,
                             CORE_WAIT, C_REQ, C_WAIT, RETIRE, DRAIN} job_state_t;
    validation_t validation;
    job_state_t state;
    logic [31:0] job_id_q, m_q, n_q, k_q, a_base_q, bt_base_q, c_base_q;
    logic [31:0] a_stride_q, bt_stride_q, c_stride_q, mode_q, watchdog_q;
    logic [31:0] watchdog_threshold;
    logic [42:0] a_size, bt_size, c_size;
    logic [43:0] a_end, bt_end, c_end;
    logic [10:0] i0, j0;
    logic [DIM_W-1:0] tile_rows, tile_cols;
    logic last_row_band, last_col_band;
    logic [31:0] a_tile_base, bt_tile_base, c_row_base, c_tile_base;
    logic [36:0] a_step, bt_step, c_step;
    logic dma_inflight, core_inflight, core_seen_busy, a_loaded, bt_loaded;
    logic [63:0] timestamp, accepted_tick, last_b_tick;
    logic tile_b_seen, had_calibration;
    logic [31:0] watchdog_count;
    logic [63:0] run_compute, run_reads, run_writes, run_bytes;
    logic [63:0] run_input_wait, run_read_stalls, run_write_stalls;

    assign busy = state != J_IDLE;
    assign ready = !rst && !busy && validation == V_IDLE && ddr_ready && !host_busy &&
                   !reset_required && !dma_fatal && !dma_busy && !compute_busy && axi_quiescent;
    assign start_ready = !rst && !start_rsp_valid && validation == V_IDLE;
    wire start_fire = start_valid && start_ready;
    assign clear_status_code = busy || validation != V_IDLE || start_fire ? BUSY_CODE :
                               reset_required ? NOT_READY : 16'd0;

    // Full-width basic checks precede the smaller multipliers. C stride uses a
    // 34-bit 4*N expression so an oversized unsigned N cannot wrap into legality.
    wire basic_invalid = m_q == 0 || m_q > 1024 || n_q == 0 || n_q > 1024 ||
        k_q == 0 || k_q > 256 || mode_q != 0 || watchdog_q == 0 ||
        a_base_q[5:0] != 0 || bt_base_q[5:0] != 0 || c_base_q[5:0] != 0 ||
        {1'b0,a_base_q} >= DDR_BYTES || {1'b0,bt_base_q} >= DDR_BYTES ||
        {1'b0,c_base_q} >= DDR_BYTES ||
        a_stride_q[5:0] != 0 || bt_stride_q[5:0] != 0 || c_stride_q[5:0] != 0 ||
        a_stride_q < k_q || bt_stride_q < k_q || {2'd0,c_stride_q} < {n_q,2'b00};
    wire allocations_invalid = a_end > {11'd0,DDR_BYTES} ||
        bt_end > {11'd0,DDR_BYTES} || c_end > {11'd0,DDR_BYTES} ||
        !(a_end <= {12'd0,bt_base_q} || bt_end <= {12'd0,a_base_q}) ||
        !(a_end <= {12'd0,c_base_q} || c_end <= {12'd0,a_base_q}) ||
        !(bt_end <= {12'd0,c_base_q} || c_end <= {12'd0,bt_base_q});

    wire active = busy && state != DRAIN;
    wire issue_allowed = !rst && !reset_required && !dma_fatal && ddr_ready;
    assign dma_req_valid = (state == A_REQ || state == BT_REQ || state == C_REQ) && issue_allowed;
    assign dma_req_write = state == C_REQ;
    assign dma_req_bt = state == BT_REQ;
    assign dma_req_buf = 1'b0;
    assign dma_req_rows = state == BT_REQ ? 6'(tile_cols) : 6'(tile_rows);
    assign dma_req_row_bytes = state == C_REQ ? 9'(tile_cols)*9'd4 : k_q[8:0];
    always_comb begin
        case (state)
            BT_REQ: begin dma_req_base=bt_tile_base; dma_req_stride=bt_stride_q; end
            C_REQ: begin dma_req_base=c_tile_base; dma_req_stride=c_stride_q; end
            default: begin dma_req_base=a_tile_base; dma_req_stride=a_stride_q; end
        endcase
    end
    wire dma_request_fire = dma_req_valid && dma_req_ready;
    assign dma_done_ready = !rst && (dma_inflight || state == DRAIN);
    wire dma_end_fire = dma_done_valid && dma_done_ready;
    assign compute_start = state == CORE_REQ && compute_ready && issue_allowed;
    assign compute_input_buf = 1'b0;
    assign compute_output_buf = 1'b0;
    assign compute_m = tile_rows;
    assign compute_n = tile_cols;
    assign compute_k = k_q[8:0];
    // Core DONE is sticky. Only a newly launched and observed-active core can
    // contribute a completion; the preceding tile's DONE cannot advance us.
    wire core_end = core_inflight && core_seen_busy && compute_done && !compute_busy;

    wire [10:0] next_i = i0 + 11'(T);
    wire [10:0] next_j = j0 + 11'(T);
    // Tile-end flags are prepared with the origins, before any DMA. Keep
    // dimension arithmetic out of final-response and first-fault snapshots.
    wire final_tile = last_row_band && last_col_band;
    wire [37:0] next_a_base = {6'd0,a_tile_base} + {1'b0,a_step};
    wire [37:0] next_bt_base = {6'd0,bt_tile_base} + {1'b0,bt_step};
    wire [37:0] next_c_row = {6'd0,c_row_base} + {1'b0,c_step};
    wire [32:0] next_c_col = {1'b0,c_tile_base} + 33'(4*T);
    wire [10:0] remaining_m = m_q[10:0] - next_i;
    wire [10:0] remaining_n = n_q[10:0] - next_j;

    wire r_fire = axi_rvalid && axi_rready;
    wire w_fire = axi_wvalid && axi_wready;
    wire b_fire = axi_bvalid && axi_bready;
    wire good_b_fire = b_fire && axi_bresp == 0;
    wire have_final_b = tile_b_seen || good_b_fire;
    wire [63:0] final_b_tick = good_b_fire ? timestamp : last_b_tick;
    wire input_waiting = state == A_REQ || state == A_WAIT || state == BT_REQ || state == BT_WAIT;
    wire progress = mem_progress || dma_request_fire || dma_end_fire || compute_start || core_end || compute_progress;
    function automatic [3:0] byte_count(input logic [7:0] mask);
        integer bit_index;
        begin
            byte_count = 0;
            for (bit_index=0; bit_index<8; bit_index=bit_index+1)
                byte_count = byte_count + {3'd0,mask[bit_index]};
        end
    endfunction
    wire [63:0] next_compute = run_compute + (core_end ? compute_cycles_in : 64'd0);
    wire [63:0] next_reads = run_reads + (r_fire ? 64'd1 : 64'd0);
    wire [63:0] next_writes = run_writes + (w_fire ? 64'd1 : 64'd0);
    wire [63:0] next_bytes = run_bytes + (w_fire ? {60'd0,byte_count(axi_wstrb)} : 64'd0);
    wire [63:0] next_input_wait = run_input_wait + (input_waiting ? 64'd1 : 64'd0);
    wire [63:0] next_read_stalls = run_read_stalls + (axi_rvalid && !axi_rready ? 64'd1 : 64'd0);
    wire [63:0] next_write_stalls = run_write_stalls + (axi_wvalid && !axi_wready ? 64'd1 : 64'd0);
    // During a fault the current compute tile may not have reached DONE yet.
    // Its live counter is meaningful only after this tile's START was accepted.
    wire [63:0] fault_compute = run_compute +
        (state == CORE_WAIT && core_inflight ? compute_cycles_in : 64'd0);

    logic [15:0] new_fault;
    always_comb begin
        new_fault = 0;
        // Startup before calibration is merely NOT_READY. Once calibrated,
        // even an idle loss invalidates host memory assumptions until reset.
        if (had_calibration && !ddr_ready) new_fault = CALIB_LOST;
        else if (dma_fatal)
            new_fault = dma_fatal_code >= 7 && dma_fatal_code <= 10 ? dma_fatal_code : PROTOCOL;
        else if (active && b_fire && axi_bresp != 0) new_fault = MEM_RESP;
        else if (active && (compute_error ||
            (dma_done_valid && (!dma_inflight || dma_done_status != 0)) ||
            (state == C_WAIT && dma_end_fire && final_tile && (!have_final_b || !axi_quiescent))))
            new_fault = PROTOCOL;
        else if (active && !progress && watchdog_count >= watchdog_threshold) new_fault = WATCHDOG;
    end
    wire job_success = state == C_WAIT && dma_end_fire && dma_done_status == 0 &&
                       final_tile && !reset_required && new_fault == 0;

    always_ff @(posedge clk) begin
        if (rst) begin
            validation <= V_IDLE; state <= J_IDLE;
            job_id_q <= 0; m_q <= 0; n_q <= 0; k_q <= 0;
            a_base_q <= 0; bt_base_q <= 0; c_base_q <= 0;
            a_stride_q <= 0; bt_stride_q <= 0; c_stride_q <= 0; mode_q <= 0; watchdog_q <= 0;
            watchdog_threshold <= 0;
            a_size <= 0; bt_size <= 0; c_size <= 0; a_end <= 0; bt_end <= 0; c_end <= 0;
            i0 <= 0; j0 <= 0; tile_rows <= 0; tile_cols <= 0;
            last_row_band <= 0; last_col_band <= 0;
            a_tile_base <= 0; bt_tile_base <= 0; c_row_base <= 0; c_tile_base <= 0;
            a_step <= 0; bt_step <= 0; c_step <= 0;
            dma_inflight <= 0; core_inflight <= 0; core_seen_busy <= 0;
            a_loaded <= 0; bt_loaded <= 0;
            timestamp <= 0; accepted_tick <= 0; last_b_tick <= 0; tile_b_seen <= 0; had_calibration <= 0; watchdog_count <= 0;
            start_rsp_valid <= 0; start_rsp_status <= 0; job_accepted <= 0;
            done <= 0; error <= 0; reset_required <= 0; error_code <= 0; last_job_id <= 0;
            job_cycles <= 0; compute_cycles <= 0; read_beats <= 0; write_beats <= 0;
            write_valid_bytes <= 0; input_wait_cycles <= 0; read_stall_cycles <= 0; write_stall_cycles <= 0;
            run_compute <= 0; run_reads <= 0; run_writes <= 0; run_bytes <= 0;
            run_input_wait <= 0; run_read_stalls <= 0; run_write_stalls <= 0;
        end else begin
            timestamp <= timestamp + 1'b1;
            if (ddr_ready) had_calibration <= 1;
            job_accepted <= 0;
            if (start_rsp_valid && start_rsp_ready) start_rsp_valid <= 0;
            if (clear_status && clear_status_code == 0) begin done <= 0; error <= 0; error_code <= 0; end

            if (busy) begin
                run_compute <= next_compute; run_reads <= next_reads; run_writes <= next_writes;
                run_bytes <= next_bytes; run_input_wait <= next_input_wait;
                run_read_stalls <= next_read_stalls; run_write_stalls <= next_write_stalls;
                if (progress) watchdog_count <= 0;
                else if (watchdog_count < watchdog_q) watchdog_count <= watchdog_count + 1'b1;
            end
            if (dma_request_fire) dma_inflight <= 1;
            if (dma_end_fire) dma_inflight <= 0;
            if (compute_start) begin core_inflight <= 1; core_seen_busy <= 0; end
            if (core_inflight && compute_busy) core_seen_busy <= 1;
            if (core_end) begin core_inflight <= 0; core_seen_busy <= 0; end

            if (start_fire) begin
                if (busy) begin start_rsp_valid <= 1; start_rsp_status <= BUSY_CODE; end
                else if (!ready) begin start_rsp_valid <= 1; start_rsp_status <= NOT_READY; end
                else begin
                    job_id_q <= cfg_job_id; m_q <= cfg_m; n_q <= cfg_n; k_q <= cfg_k;
                    a_base_q <= cfg_a_base; bt_base_q <= cfg_bt_base; c_base_q <= cfg_c_base;
                    a_stride_q <= cfg_a_stride; bt_stride_q <= cfg_bt_stride; c_stride_q <= cfg_c_stride;
                    mode_q <= cfg_mode; watchdog_q <= cfg_watchdog;
                    // Validation rejects zero. Prepare the threshold here so
                    // subtraction is absent from the active fault-snapshot path.
                    watchdog_threshold <= cfg_watchdog - 32'd1;
                    validation <= V_BASIC;
                end
            end
            case (validation)
                V_BASIC: if (basic_invalid) begin
                    start_rsp_valid <= 1; start_rsp_status <= BAD_DESC; validation <= V_IDLE;
                    error <= 1; error_code <= BAD_DESC;
                end else validation <= V_PRODUCT;
                V_PRODUCT: begin
                    a_size <= {32'd0,m_q[10:0]} * {11'd0,a_stride_q};
                    bt_size <= {32'd0,n_q[10:0]} * {11'd0,bt_stride_q};
                    c_size <= {32'd0,m_q[10:0]} * {11'd0,c_stride_q};
                    validation <= V_ADD;
                end
                V_ADD: begin
                    a_end <= {12'd0,a_base_q} + {1'b0,a_size};
                    bt_end <= {12'd0,bt_base_q} + {1'b0,bt_size};
                    c_end <= {12'd0,c_base_q} + {1'b0,c_size};
                    validation <= V_CHECK;
                end
                V_CHECK: begin
                    start_rsp_valid <= 1; validation <= V_IDLE;
                    if (allocations_invalid) begin start_rsp_status <= BAD_DESC; error <= 1; error_code <= BAD_DESC; end
                    else if (!ddr_ready || host_busy || reset_required || dma_fatal || dma_busy || compute_busy || !axi_quiescent)
                        start_rsp_status <= NOT_READY;
                    else begin
                        start_rsp_status <= 0; job_accepted <= 1; last_job_id <= job_id_q;
                        // job_accepted is registered: it is visible AFTER this
                        // acceptance edge, whose pre-increment timestamp is saved.
                        accepted_tick <= timestamp; watchdog_count <= 0;
                        done <= 0; error <= 0; error_code <= 0;
                        state <= A_REQ; i0 <= 0; j0 <= 0;
                        last_row_band <= m_q <= T;
                        last_col_band <= n_q <= T;
                        tile_rows <= m_q > T ? DIM_W'(T) : DIM_W'(m_q);
                        tile_cols <= n_q > T ? DIM_W'(T) : DIM_W'(n_q);
                        a_tile_base <= a_base_q; bt_tile_base <= bt_base_q;
                        c_row_base <= c_base_q; c_tile_base <= c_base_q;
                        a_step <= {5'd0,a_stride_q} << $clog2(T);
                        bt_step <= {5'd0,bt_stride_q} << $clog2(T);
                        c_step <= {5'd0,c_stride_q} << $clog2(T);
                        a_loaded <= 0; bt_loaded <= 0; tile_b_seen <= 0;
                        job_cycles <= 0; compute_cycles <= 0; read_beats <= 0; write_beats <= 0;
                        write_valid_bytes <= 0; input_wait_cycles <= 0; read_stall_cycles <= 0; write_stall_cycles <= 0;
                        run_compute <= 0; run_reads <= 0; run_writes <= 0; run_bytes <= 0;
                        run_input_wait <= 0; run_read_stalls <= 0; run_write_stalls <= 0;
                    end
                end
                default: begin end
            endcase

            case (state)
                A_REQ: if (dma_request_fire) state <= A_WAIT;
                A_WAIT: if (dma_end_fire) begin a_loaded <= 1; state <= BT_REQ; end
                BT_REQ: if (dma_request_fire) state <= BT_WAIT;
                BT_WAIT: if (dma_end_fire) begin bt_loaded <= 1; state <= CORE_REQ; end
                CORE_REQ: if (compute_start) state <= CORE_WAIT;
                CORE_WAIT: if (core_end) state <= C_REQ;
                C_REQ: if (dma_request_fire) begin tile_b_seen <= 0; state <= C_WAIT; end
                C_WAIT: if (dma_end_fire) begin
                    if (final_tile) begin
                        state <= J_IDLE; done <= 1;
                        job_cycles <= final_b_tick - accepted_tick;
                        compute_cycles <= next_compute; read_beats <= next_reads; write_beats <= next_writes;
                        write_valid_bytes <= next_bytes; input_wait_cycles <= next_input_wait;
                        read_stall_cycles <= next_read_stalls; write_stall_cycles <= next_write_stalls;
                    end else state <= RETIRE;
                end
                RETIRE: begin
                    a_loaded <= 0; bt_loaded <= 0;
                    if (!last_col_band) begin
                        j0 <= next_j; bt_tile_base <= next_bt_base[31:0]; c_tile_base <= next_c_col[31:0];
                        tile_cols <= remaining_n > T ? DIM_W'(T) : DIM_W'(remaining_n);
                        last_col_band <= remaining_n <= T;
                    end else begin
                        i0 <= next_i; j0 <= 0; a_tile_base <= next_a_base[31:0];
                        bt_tile_base <= bt_base_q; c_row_base <= next_c_row[31:0]; c_tile_base <= next_c_row[31:0];
                        tile_rows <= remaining_m > T ? DIM_W'(T) : DIM_W'(remaining_m);
                        tile_cols <= n_q > T ? DIM_W'(T) : DIM_W'(n_q);
                        last_row_band <= remaining_m <= T;
                        last_col_band <= n_q <= T;
                    end
                    state <= A_REQ;
                end
                DRAIN: if (!dma_inflight && !core_inflight && !dma_busy && !compute_busy && axi_quiescent)
                    state <= J_IDLE;
                default: begin end
            endcase
            if (busy && good_b_fire) begin last_b_tick <= timestamp; tile_b_seen <= 1; end

            // This registered latch is the only job fault feedback to DMA.
            // It must not be replaced by combinational new_fault feedback.
            if (!reset_required && new_fault != 0) begin
                error <= 1; error_code <= new_fault; reset_required <= 1; done <= 0;
                if (busy || dma_busy || compute_busy || !axi_quiescent) state <= DRAIN;
                if (validation != V_IDLE || (start_fire && !busy)) begin
                    validation <= V_IDLE; start_rsp_valid <= 1; start_rsp_status <= NOT_READY;
                end
                // Public fault counters freeze once: AXI handshakes on this
                // edge count; the active core counter is sampled before its
                // concurrent update. Private counters may continue draining.
                if (busy) begin
                    job_cycles <= timestamp - accepted_tick;
                    compute_cycles <= fault_compute; read_beats <= next_reads; write_beats <= next_writes;
                    write_valid_bytes <= next_bytes; input_wait_cycles <= next_input_wait;
                    read_stall_cycles <= next_read_stalls; write_stall_cycles <= next_write_stalls;
                end
            end
        end
    end

`ifndef SYNTHESIS
    initial if ((P != 4 && P != 8) || (T != 8 && T != 32) || DIM_W != $clog2(T+1) ||
                DDR_BYTES == 0 || DDR_BYTES > 33'h100000000)
        $fatal(1,"Unsupported DDR job configuration");
    logic held_dma, held_rsp;
    logic [81:0] previous_dma;
    logic [15:0] previous_rsp;
    always @(posedge clk) begin
        if (rst) begin held_dma <= 0; held_rsp <= 0; end
        else begin
            // Fatal withdrawal is only of an unaccepted local DMA request.
            // Accepted operations and their AXI obligations continue in DRAIN.
            if (held_dma && issue_allowed && (!dma_req_valid ||
                {dma_req_write,dma_req_bt,dma_req_buf,dma_req_base,dma_req_stride,dma_req_rows,dma_req_row_bytes} !== previous_dma))
                $fatal(1,"DDR job DMA request changed while stalled");
            if (held_rsp && (!start_rsp_valid || start_rsp_status !== previous_rsp))
                $fatal(1,"DDR START response changed while stalled");
            if (compute_start && (!a_loaded || !bt_loaded || dma_inflight || dma_busy))
                $fatal(1,"DDR compute launched before operand loads completed");
            if ((dma_req_valid && (compute_busy || core_inflight)) || (dma_inflight && core_inflight))
                $fatal(1,"Serial DDR job overlapped DMA and compute");
            if (dma_req_buf || compute_input_buf || compute_output_buf)
                $fatal(1,"Serial DDR job changed buffer set");
            if (job_success && (!have_final_b || !axi_quiescent))
                $fatal(1,"DDR job succeeded before the final write acknowledgement");
            if (active && (last_row_band !== (next_i >= m_q[10:0]) ||
                           last_col_band !== (next_j >= n_q[10:0])))
                $fatal(1,"DDR tile-end flags disagree with the current origins");
            if (state == RETIRE && !reset_required &&
                ((!last_col_band && (next_bt_base[37:32] != 0 || next_c_col[32])) ||
                 (last_col_band && (next_a_base[37:32] != 0 || next_c_row[37:32] != 0))))
                $fatal(1,"Validated DDR tile address overflowed");
            held_dma <= dma_req_valid && !dma_req_ready;
            held_rsp <= start_rsp_valid && !start_rsp_ready;
            previous_dma <= {dma_req_write,dma_req_bt,dma_req_buf,dma_req_base,dma_req_stride,dma_req_rows,dma_req_row_bytes};
            previous_rsp <= start_rsp_status;
        end
    end
`endif
endmodule
