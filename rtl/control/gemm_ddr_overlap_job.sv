`timescale 1ns/1ps
// Descriptor validation, status and measured counters around the tagged tile
// scheduler. Reset must also reset DMA, AXI conversion and the memory platform.
module gemm_ddr_overlap_job #(
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
    output wire load_req_valid,
    input wire load_req_ready,
    output wire load_req_bt, load_req_buf,
    output wire [31:0] load_req_base, load_req_stride,
    output wire [5:0] load_req_rows,
    output wire [8:0] load_req_row_bytes,
    input wire load_busy, load_done_valid,
    input wire [15:0] load_done_status,
    output wire load_done_ready,
    output wire store_req_valid,
    input wire store_req_ready,
    output wire store_req_buf,
    output wire [31:0] store_req_base, store_req_stride,
    output wire [5:0] store_req_rows,
    output wire [8:0] store_req_row_bytes,
    input wire store_busy, store_done_valid,
    input wire [15:0] store_done_status,
    output wire store_done_ready,
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
    input wire axi_quiescent, mem_progress, burst_local_idle
);
    localparam logic [15:0] BAD_DESC=3, BUSY_CODE=4, NOT_READY=5,
                            MEM_RESP=7, PROTOCOL=8, WATCHDOG=9, CALIB_LOST=10;
    typedef enum logic [2:0] {V_IDLE, V_BASIC, V_PRODUCT, V_ADD, V_CHECK} validation_t;
    typedef enum logic [1:0] {J_IDLE, J_RUN, J_DRAIN} job_state_t;
    validation_t validation;
    job_state_t state;
    logic [31:0] job_id_q, m_q, n_q, k_q, a_base_q, bt_base_q, c_base_q;
    logic [31:0] a_stride_q, bt_stride_q, c_stride_q, mode_q, watchdog_q, watchdog_threshold;
    logic [42:0] a_size, bt_size, c_size;
    logic [43:0] a_end, bt_end, c_end;
    logic [63:0] timestamp, accepted_tick, last_b_tick;
    logic b_seen, had_calibration;
    logic [31:0] watchdog_count;
    logic [63:0] run_compute, run_reads, run_writes, run_bytes;
    logic [63:0] run_input_wait, run_read_stalls, run_write_stalls;
    wire scheduler_job_ready, scheduler_busy, scheduler_done, scheduler_fatal;
    wire [15:0] scheduler_fatal_code, scheduler_detected_fault_code;
    wire scheduler_compute_complete, scheduler_compute_inflight, scheduler_input_waiting, scheduler_detected_fault;

    function automatic [15:0] normalize_fault(input logic [15:0] code);
        case (code)
            16'd7, 16'd8, 16'd9, 16'd10: normalize_fault = code;
            default: normalize_fault = PROTOCOL;
        endcase
    endfunction

    assign busy = state != J_IDLE;
    wire downstream_idle = !load_busy && !store_busy && !compute_busy && burst_local_idle && axi_quiescent;
    wire calibration_lost = had_calibration && !ddr_ready;
    wire platform_available = !rst && ddr_ready && !host_busy && !reset_required &&
                              !dma_fatal && !scheduler_fatal && !scheduler_busy && downstream_idle;
    assign ready = !busy && validation == V_IDLE && platform_available && !scheduler_detected_fault;
    assign start_ready = !rst && !start_rsp_valid && validation == V_IDLE;
    wire start_fire = start_valid && start_ready;
    assign clear_status_code = busy || validation != V_IDLE || start_fire ? BUSY_CODE :
        reset_required || dma_fatal || scheduler_fatal || calibration_lost || scheduler_detected_fault ?
        NOT_READY : 16'd0;

    // Validate full unsigned fields before narrowing dimensions for multiply.
    // Allocations conservatively cover rows*stride, including row padding.
    wire basic_invalid = m_q == 0 || m_q > 1024 || n_q == 0 || n_q > 1024 ||
        k_q == 0 || k_q > 256 || mode_q > 1 || watchdog_q == 0 ||
        a_base_q[5:0] != 0 || bt_base_q[5:0] != 0 || c_base_q[5:0] != 0 ||
        {1'b0,a_base_q} >= DDR_BYTES || {1'b0,bt_base_q} >= DDR_BYTES || {1'b0,c_base_q} >= DDR_BYTES ||
        a_stride_q[5:0] != 0 || bt_stride_q[5:0] != 0 || c_stride_q[5:0] != 0 ||
        a_stride_q < k_q || bt_stride_q < k_q || {2'd0,c_stride_q} < {n_q,2'b00};
    wire allocations_invalid = a_end > {11'd0,DDR_BYTES} || bt_end > {11'd0,DDR_BYTES} ||
        c_end > {11'd0,DDR_BYTES} ||
        !(a_end <= {12'd0,bt_base_q} || bt_end <= {12'd0,a_base_q}) ||
        !(a_end <= {12'd0,c_base_q} || c_end <= {12'd0,a_base_q}) ||
        !(bt_end <= {12'd0,c_base_q} || c_end <= {12'd0,bt_base_q});
    // The validated descriptor is accepted by the scheduler on this same edge.
    // Neither START reception nor its held response begins the measured epoch.
    wire scheduler_job_valid = validation == V_CHECK && !allocations_invalid &&
                               platform_available && !scheduler_detected_fault;
    wire scheduler_job_fire = scheduler_job_valid && scheduler_job_ready;
    // This is the only shell feedback to scheduling. Watchdog detection must
    // remain registered, because progress depends on scheduling handshakes.
    // Calibration loss is an independent simple input and stops new offers
    // immediately, while previously accepted memory/core work remains owed.
    wire scheduler_mem_fatal = reset_required || dma_fatal || calibration_lost;
    wire [15:0] scheduler_mem_code = reset_required ? error_code : calibration_lost ? CALIB_LOST :
                                    dma_fatal ? normalize_fault(dma_fatal_code) : 16'd0;
    gemm_tile_scheduler #(.P(P), .T(T), .DIM_W(DIM_W)) scheduler (
        .clk(clk), .rst(rst), .job_valid(scheduler_job_valid), .job_ready(scheduler_job_ready),
        .cfg_m(m_q), .cfg_n(n_q), .cfg_k(k_q),
        .cfg_a_base(a_base_q), .cfg_bt_base(bt_base_q), .cfg_c_base(c_base_q),
        .cfg_a_stride(a_stride_q), .cfg_bt_stride(bt_stride_q), .cfg_c_stride(c_stride_q), .cfg_mode(mode_q),
        .busy(scheduler_busy), .done(scheduler_done), .fatal(scheduler_fatal), .fatal_code(scheduler_fatal_code),
        .mem_fatal(scheduler_mem_fatal), .mem_fatal_code(scheduler_mem_code),
        .load_busy(load_busy), .store_busy(store_busy), .burst_local_idle(burst_local_idle), .axi_quiescent(axi_quiescent),
        .load_req_valid(load_req_valid), .load_req_ready(load_req_ready), .load_req_bt(load_req_bt), .load_req_buf(load_req_buf),
        .load_req_base(load_req_base), .load_req_stride(load_req_stride),
        .load_req_rows(load_req_rows), .load_req_row_bytes(load_req_row_bytes),
        .load_done_valid(load_done_valid), .load_done_ready(load_done_ready), .load_done_status(load_done_status),
        .store_req_valid(store_req_valid), .store_req_ready(store_req_ready), .store_req_buf(store_req_buf),
        .store_req_base(store_req_base), .store_req_stride(store_req_stride),
        .store_req_rows(store_req_rows), .store_req_row_bytes(store_req_row_bytes),
        .store_done_valid(store_done_valid), .store_done_ready(store_done_ready), .store_done_status(store_done_status),
        .compute_start(compute_start), .compute_ready(compute_ready), .compute_busy(compute_busy),
        .compute_done(compute_done), .compute_error(compute_error),
        .compute_input_buf(compute_input_buf), .compute_output_buf(compute_output_buf),
        .compute_m(compute_m), .compute_n(compute_n), .compute_k(compute_k),
        .compute_complete(scheduler_compute_complete), .compute_inflight(scheduler_compute_inflight),
        .input_waiting(scheduler_input_waiting), .detected_fault_code(scheduler_detected_fault_code),
        .detected_fault(scheduler_detected_fault)
    );

    wire active = state == J_RUN;
    wire r_fire = axi_rvalid && axi_rready;
    wire w_fire = axi_wvalid && axi_wready;
    wire b_fire = axi_bvalid && axi_bready;
    wire good_b_fire = b_fire && axi_bresp == 0;
    wire have_final_b = b_seen || good_b_fire;
    wire [63:0] final_b_tick = good_b_fire ? timestamp : last_b_tick;
    wire load_fire = load_req_valid && load_req_ready;
    wire store_fire = store_req_valid && store_req_ready;
    wire load_end = load_done_valid && load_done_ready;
    wire store_end = store_done_valid && store_done_ready;
    wire progress = mem_progress || r_fire || w_fire || b_fire || load_fire || store_fire || load_end || store_end ||
                    compute_start || scheduler_compute_complete || compute_progress;
    function automatic [3:0] byte_count(input logic [7:0] mask);
        integer bit_index;
        begin
            byte_count = 0;
            for (bit_index=0; bit_index<8; bit_index=bit_index+1)
                byte_count = byte_count + {3'd0,mask[bit_index]};
        end
    endfunction
    wire [63:0] next_compute = run_compute + (scheduler_compute_complete ? compute_cycles_in : 64'd0);
    wire [63:0] next_reads = run_reads + (r_fire ? 64'd1 : 64'd0);
    wire [63:0] next_writes = run_writes + (w_fire ? 64'd1 : 64'd0);
    wire [63:0] next_bytes = run_bytes + (w_fire ? {60'd0,byte_count(axi_wstrb)} : 64'd0);
    wire [63:0] next_input_wait = run_input_wait + (scheduler_input_waiting ? 64'd1 : 64'd0);
    wire [63:0] next_read_stalls = run_read_stalls + (axi_rvalid && !axi_rready ? 64'd1 : 64'd0);
    wire [63:0] next_write_stalls = run_write_stalls + (axi_wvalid && !axi_wready ? 64'd1 : 64'd0);
    // Completion and fatal detection on one edge still count that tile once.
    // An accepted but unfinished core contributes only its current live count.
    wire [63:0] fault_compute = run_compute + (scheduler_compute_inflight ? compute_cycles_in : 64'd0);
    wire completion_candidate = active && scheduler_done && !scheduler_busy;
    wire failed_b = active && b_fire && axi_bresp != 0;
    wire invalid_completion = completion_candidate && (!have_final_b || !downstream_idle);
    wire watchdog_expired = active && !progress && watchdog_count >= watchdog_threshold;
    // Diagnostic payload selection never controls admission or the 512-bit
    // counter snapshot enable. Fault detection itself has no added latency.
    wire new_fault_detected = calibration_lost || dma_fatal || failed_b || scheduler_detected_fault ||
                              scheduler_fatal || invalid_completion || watchdog_expired;
    logic [15:0] new_fault;
    always_comb begin
        new_fault = 0;
        if (calibration_lost) new_fault = CALIB_LOST;
        else if (dma_fatal) new_fault = normalize_fault(dma_fatal_code);
        else if (failed_b) new_fault = MEM_RESP;
        else if (scheduler_detected_fault) new_fault = normalize_fault(scheduler_detected_fault_code);
        else if (scheduler_fatal) new_fault = normalize_fault(scheduler_fatal_code);
        else if (invalid_completion) new_fault = PROTOCOL;
        else if (watchdog_expired) new_fault = WATCHDOG;
    end
    wire job_success = completion_candidate && have_final_b && downstream_idle && !reset_required && !new_fault_detected;

    always_ff @(posedge clk) begin
        if (rst) begin
            validation <= V_IDLE; state <= J_IDLE;
            job_id_q <= 0; m_q <= 0; n_q <= 0; k_q <= 0;
            a_base_q <= 0; bt_base_q <= 0; c_base_q <= 0;
            a_stride_q <= 0; bt_stride_q <= 0; c_stride_q <= 0; mode_q <= 0;
            watchdog_q <= 0; watchdog_threshold <= 0;
            a_size <= 0; bt_size <= 0; c_size <= 0; a_end <= 0; bt_end <= 0; c_end <= 0;
            timestamp <= 0; accepted_tick <= 0; last_b_tick <= 0; b_seen <= 0;
            had_calibration <= 0; watchdog_count <= 0;
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
            if (busy && good_b_fire) begin last_b_tick <= timestamp; b_seen <= 1; end
            if (start_fire) begin
                if (busy) begin start_rsp_valid <= 1; start_rsp_status <= BUSY_CODE; end
                else if (!ready) begin start_rsp_valid <= 1; start_rsp_status <= NOT_READY; end
                else begin
                    job_id_q <= cfg_job_id; m_q <= cfg_m; n_q <= cfg_n; k_q <= cfg_k;
                    a_base_q <= cfg_a_base; bt_base_q <= cfg_bt_base; c_base_q <= cfg_c_base;
                    a_stride_q <= cfg_a_stride; bt_stride_q <= cfg_bt_stride; c_stride_q <= cfg_c_stride;
                    mode_q <= cfg_mode; watchdog_q <= cfg_watchdog;
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
                    if (allocations_invalid) begin
                        start_rsp_status <= BAD_DESC; error <= 1; error_code <= BAD_DESC;
                    end else if (!scheduler_job_fire) start_rsp_status <= NOT_READY;
                    else begin
                        start_rsp_status <= 0; job_accepted <= 1; last_job_id <= job_id_q;
                        accepted_tick <= timestamp; last_b_tick <= 0; b_seen <= 0; watchdog_count <= 0;
                        done <= 0; error <= 0; error_code <= 0;
                        state <= J_RUN;
                        job_cycles <= 0; compute_cycles <= 0; read_beats <= 0; write_beats <= 0;
                        write_valid_bytes <= 0; input_wait_cycles <= 0; read_stall_cycles <= 0; write_stall_cycles <= 0;
                        run_compute <= 0; run_reads <= 0; run_writes <= 0; run_bytes <= 0;
                        run_input_wait <= 0; run_read_stalls <= 0; run_write_stalls <= 0;
                    end
                end
                default: begin end
            endcase
            if (job_success) begin
                state <= J_IDLE; done <= 1;
                // Scheduler completion is later than the final B edge. Preserve
                // the memory-completion timestamp, not the drain/check latency.
                job_cycles <= final_b_tick - accepted_tick;
                compute_cycles <= next_compute; read_beats <= next_reads; write_beats <= next_writes;
                write_valid_bytes <= next_bytes; input_wait_cycles <= next_input_wait;
                read_stall_cycles <= next_read_stalls; write_stall_cycles <= next_write_stalls;
            end else if (state == J_DRAIN && !scheduler_busy && downstream_idle) state <= J_IDLE;

            // Registered first fault is the only watchdog/status feedback. Do
            // not replace this with a combinational progress-dependent stop.
            // Offered START responses remain immutable once already visible.
            if (!reset_required && new_fault_detected) begin
                error <= 1; error_code <= new_fault; reset_required <= 1; done <= 0;
                state <= J_DRAIN;
                if (validation != V_IDLE || (start_fire && !busy)) begin
                    validation <= V_IDLE; start_rsp_valid <= 1; start_rsp_status <= NOT_READY;
                end
                // Include raw accepted R/W/strobes/stalls on this fault edge;
                // freeze public data once while private obligations keep draining.
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
        $fatal(1,"Unsupported overlap job configuration");
    logic held_rsp, previous_fatal;
    logic [15:0] previous_rsp, previous_error;
    logic [511:0] previous_counters;
    wire [511:0] public_counters = {job_cycles,compute_cycles,read_beats,write_beats,
                                   write_valid_bytes,input_wait_cycles,read_stall_cycles,write_stall_cycles};
    always @(posedge clk) begin
        if (rst) begin held_rsp <= 0; previous_fatal <= 0; previous_error <= 0; previous_counters <= 0; end
        else begin
            if (new_fault_detected !== (new_fault != 0))
                $fatal(1,"Overlap job fault predicate differs from diagnostic payload");
            if (held_rsp && (!start_rsp_valid || start_rsp_status !== previous_rsp))
                $fatal(1,"Overlap START response changed while stalled");
            if (previous_fatal && (!reset_required || error_code !== previous_error || public_counters !== previous_counters))
                $fatal(1,"Overlap first fault or public counters changed after capture");
            if (validation != V_IDLE && (load_req_valid || store_req_valid || compute_start))
                $fatal(1,"Overlap job issued work before validation and scheduler acceptance");
            if (scheduler_job_fire && (busy || !downstream_idle || reset_required || host_busy || !ddr_ready))
                $fatal(1,"Overlap scheduler accepted an unsafe job");
            if (reset_required && (load_req_valid || store_req_valid || compute_start))
                $fatal(1,"Overlap job issued a new descriptor or launch after fatal capture");
            if (compute_start && !ddr_ready)
                $fatal(1,"Overlap compute launched after calibration loss");
            if (job_success && (!have_final_b || !downstream_idle || scheduler_busy))
                $fatal(1,"Overlap job succeeded before final write/drain completion");
            held_rsp <= start_rsp_valid && !start_rsp_ready;
            previous_rsp <= start_rsp_status;
            previous_fatal <= reset_required; previous_error <= error_code; previous_counters <= public_counters;
        end
    end
`endif
endmodule
