`timescale 1ns/1ps
// Internal scheduler for an already validated descriptor. The caller supplies
// watchdog, calibration/host exclusion, descriptor validation and job counters.
// The selectable overlap job shell supplies these outer-system guarantees.
module gemm_tile_scheduler #(
    parameter integer P=4, T=32, DIM_W=$clog2(T+1)
) (
    input wire clk, rst, job_valid,
    output wire job_ready,
    input wire [31:0] cfg_m, cfg_n, cfg_k,
    input wire [31:0] cfg_a_base, cfg_bt_base, cfg_c_base,
    input wire [31:0] cfg_a_stride, cfg_bt_stride, cfg_c_stride, cfg_mode,
    output wire busy,
    output logic done,
    output wire fatal,
    output wire [15:0] fatal_code,
    input wire mem_fatal,
    input wire [15:0] mem_fatal_code,
    input wire load_busy, store_busy, burst_local_idle, axi_quiescent,
    output wire load_req_valid,
    input wire load_req_ready,
    output wire load_req_bt, load_req_buf,
    output wire [31:0] load_req_base, load_req_stride,
    output wire [5:0] load_req_rows,
    output wire [8:0] load_req_row_bytes,
    input wire load_done_valid,
    output wire load_done_ready,
    input wire [15:0] load_done_status,
    output wire store_req_valid,
    input wire store_req_ready,
    output wire store_req_buf,
    output wire [31:0] store_req_base, store_req_stride,
    output wire [5:0] store_req_rows,
    output wire [8:0] store_req_row_bytes,
    input wire store_done_valid,
    output wire store_done_ready,
    input wire [15:0] store_done_status,
    output wire compute_start,
    input wire compute_ready, compute_busy, compute_done, compute_error,
    output wire compute_input_buf, compute_output_buf,
    output wire [DIM_W-1:0] compute_m, compute_n,
    output wire [8:0] compute_k,
    // Compute observation and immediate fault telemetry. The registered fatal
    // output remains the feedback boundary to memory movement.
    output wire compute_complete, compute_inflight, input_waiting,
    output wire [15:0] detected_fault_code,
    output wire detected_fault
);
    localparam logic [15:0] PROTOCOL=16'd8;
    typedef enum logic [1:0] {JOB_IDLE, JOB_RUN, JOB_DRAIN} job_state_t;
    typedef enum logic [2:0] {LOAD_IDLE, LOAD_A_OFFER, LOAD_A_WAIT,
                              LOAD_BT_OFFER, LOAD_BT_WAIT} load_state_t;
    typedef enum logic [1:0] {STORE_IDLE, STORE_OFFER, STORE_WAIT} store_state_t;
    typedef enum logic [1:0] {CORE_IDLE, CORE_OFFER, CORE_WAIT} core_state_t;
    typedef enum logic [1:0] {INPUT_FREE, INPUT_FILLING, INPUT_READY,
                              INPUT_COMPUTING} input_state_t;
    typedef enum logic [1:0] {OUTPUT_FREE, OUTPUT_COMPUTING,
                              OUTPUT_READY, OUTPUT_WRITING} output_state_t;
    job_state_t job_state;
    load_state_t load_state;
    store_state_t store_state;
    core_state_t core_state;
    input_state_t input_state [0:1];
    output_state_t output_state [0:1];
    logic first_fault_q;
    logic [15:0] first_code_q;
    logic [31:0] m_q, n_q, k_q, bt_base_q;
    logic [31:0] a_stride_q, bt_stride_q, c_stride_q;
    logic overlap_q;
    // The largest T8 job has 16,384 tiles. Cursors also represent one past it.
    logic [15:0] total_tiles, admission_count, compute_count, retirement_count;
    logic [14:0] input_tag [0:1], output_tag [0:1];
    logic [31:0] input_i [0:1], input_j [0:1], output_i [0:1], output_j [0:1];
    logic [DIM_W-1:0] input_rows [0:1], input_cols [0:1];
    logic [DIM_W-1:0] output_rows [0:1], output_cols [0:1];
    logic [31:0] input_a_base [0:1], input_bt_base [0:1], input_c_base [0:1];
    logic [31:0] output_c_base [0:1];
    logic [31:0] admit_i, admit_j, admit_a_base, admit_bt_base, admit_c_row, admit_c_base;
    logic load_buf_q, store_buf_q, core_input_q, core_output_q, core_seen_busy;

    function automatic [15:0] normalize_fault(input logic [15:0] code);
        case (code)
            16'd7, 16'd8, 16'd9, 16'd10: normalize_fault = code;
            default: normalize_fault = PROTOCOL;
        endcase
    endfunction

    wire load_waiting = load_state == LOAD_A_WAIT || load_state == LOAD_BT_WAIT;
    wire store_waiting = store_state == STORE_WAIT;
    // Consume even an unexpected terminal so fatal draining cannot depend on
    // a successful-response path. A terminal may not precede request acceptance.
    assign load_done_ready = !rst;
    assign store_done_ready = !rst;
    wire load_end = load_done_valid && load_done_ready;
    wire store_end = store_done_valid && store_done_ready;
    wire unexpected_load = load_done_valid && !load_waiting;
    wire failed_load = load_end && load_done_status != 0;
    wire unexpected_store = store_done_valid && !store_waiting;
    wire failed_store = store_end && store_done_status != 0;
    // Stop control must not pass through diagnostic-code normalization and
    // priority muxes. Both paths describe the same fault on the same edge.
    assign detected_fault = mem_fatal || compute_error || unexpected_load ||
                            failed_load || unexpected_store || failed_store;
    logic [15:0] new_fault;
    always_comb begin
        new_fault = 16'd0;
        if (mem_fatal) new_fault = normalize_fault(mem_fatal_code);
        else if (compute_error) new_fault = PROTOCOL;
        else if (unexpected_load) new_fault = PROTOCOL;
        else if (failed_load) new_fault = normalize_fault(load_done_status);
        else if (unexpected_store) new_fault = PROTOCOL;
        else if (failed_store) new_fault = normalize_fault(store_done_status);
    end
    wire stop_now = first_fault_q || detected_fault;
    // Registered only: this fault output may feed the DMA whose fault output
    // returns through mem_fatal. Immediate fault detection gates local offers.
    assign fatal = first_fault_q;
    assign fatal_code = first_fault_q ? first_code_q : 16'd0;
    assign busy = job_state != JOB_IDLE;
    wire contexts_idle = load_state == LOAD_IDLE && store_state == STORE_IDLE && core_state == CORE_IDLE;
    wire downstream_idle = !load_busy && !store_busy && !compute_busy && burst_local_idle && axi_quiescent;
    assign job_ready = !rst && job_state == JOB_IDLE && !stop_now && contexts_idle && downstream_idle;
    wire job_fire = job_valid && job_ready;
    wire issue_allowed = !rst && job_state == JOB_RUN && !stop_now;

    assign load_req_valid = issue_allowed && (load_state == LOAD_A_OFFER || load_state == LOAD_BT_OFFER);
    assign load_req_bt = load_state == LOAD_BT_OFFER;
    assign load_req_buf = load_buf_q;
    assign load_req_base = load_req_bt ? input_bt_base[load_buf_q] : input_a_base[load_buf_q];
    assign load_req_stride = load_req_bt ? bt_stride_q : a_stride_q;
    assign load_req_rows = load_req_bt ? 6'(input_cols[load_buf_q]) : 6'(input_rows[load_buf_q]);
    assign load_req_row_bytes = k_q[8:0];
    wire load_fire = load_req_valid && load_req_ready;
    assign store_req_valid = issue_allowed && store_state == STORE_OFFER;
    assign store_req_buf = store_buf_q;
    assign store_req_base = output_c_base[store_buf_q];
    assign store_req_stride = c_stride_q;
    assign store_req_rows = 6'(output_rows[store_buf_q]);
    assign store_req_row_bytes = 9'(output_cols[store_buf_q]) * 9'd4;
    wire store_fire = store_req_valid && store_req_ready;
    // START is a pulse, not a stalled VALID: gemm_tile_engine rejects START
    // when not ready. Metadata is reserved and held while CORE_OFFER waits.
    assign compute_start = issue_allowed && core_state == CORE_OFFER && compute_ready;
    assign compute_input_buf = core_input_q;
    assign compute_output_buf = core_output_q;
    assign compute_m = input_rows[core_input_q];
    assign compute_n = input_cols[core_input_q];
    assign compute_k = k_q[8:0];
    wire core_end = core_state == CORE_WAIT && core_seen_busy && compute_done && !compute_busy;
    assign compute_complete = core_end;
    assign compute_inflight = core_state == CORE_WAIT;
    assign detected_fault_code = new_fault;

    wire free_input = input_state[0] == INPUT_FREE || input_state[1] == INPUT_FREE;
    wire free_input_id = input_state[0] == INPUT_FREE ? 1'b0 : 1'b1;
    wire ready_input0 = input_state[0] == INPUT_READY && {1'b0,input_tag[0]} == compute_count;
    wire ready_input1 = input_state[1] == INPUT_READY && {1'b0,input_tag[1]} == compute_count;
    wire ready_input_id = ready_input0 ? 1'b0 : 1'b1;
    wire free_output = output_state[0] == OUTPUT_FREE || output_state[1] == OUTPUT_FREE;
    wire free_output_id = output_state[0] == OUTPUT_FREE ? 1'b0 : 1'b1;
    wire ready_output0 = output_state[0] == OUTPUT_READY && {1'b0,output_tag[0]} == retirement_count;
    wire ready_output1 = output_state[1] == OUTPUT_READY && {1'b0,output_tag[1]} == retirement_count;
    wire ready_output_id = ready_output0 ? 1'b0 : 1'b1;
    // Count a pending compute launch held specifically for input readiness.
    // Serial mode does not charge the intentional wait for store retirement.
    assign input_waiting = job_state == JOB_RUN && core_state == CORE_IDLE &&
        compute_count < total_tiles && free_output && !(ready_input0 || ready_input1) &&
        (overlap_q || compute_count == retirement_count);
    wire [31:0] remaining_rows = m_q - admit_i;
    wire [31:0] remaining_cols = n_q - admit_j;
    wire [DIM_W-1:0] admit_rows = remaining_rows >= T ? DIM_W'(T) : DIM_W'(remaining_rows);
    wire [DIM_W-1:0] admit_cols = remaining_cols >= T ? DIM_W'(T) : DIM_W'(remaining_cols);
    wire all_buffers_free = input_state[0] == INPUT_FREE && input_state[1] == INPUT_FREE &&
                            output_state[0] == OUTPUT_FREE && output_state[1] == OUTPUT_FREE;

    integer buffer_index;
    always_ff @(posedge clk) begin
        if (rst) begin
            job_state <= JOB_IDLE; load_state <= LOAD_IDLE;
            store_state <= STORE_IDLE; core_state <= CORE_IDLE;
            first_fault_q <= 0; first_code_q <= 0; done <= 0;
            m_q <= 0; n_q <= 0; k_q <= 0; bt_base_q <= 0;
            a_stride_q <= 0; bt_stride_q <= 0; c_stride_q <= 0; overlap_q <= 0;
            total_tiles <= 0; admission_count <= 0; compute_count <= 0; retirement_count <= 0;
            admit_i <= 0; admit_j <= 0; admit_a_base <= 0; admit_bt_base <= 0;
            admit_c_row <= 0; admit_c_base <= 0;
            load_buf_q <= 0; store_buf_q <= 0; core_input_q <= 0; core_output_q <= 0;
            core_seen_busy <= 0;
            for (buffer_index=0; buffer_index<2; buffer_index=buffer_index+1) begin
                input_state[buffer_index] <= INPUT_FREE;
                output_state[buffer_index] <= OUTPUT_FREE;
                input_tag[buffer_index] <= 0; output_tag[buffer_index] <= 0;
                input_i[buffer_index] <= 0; input_j[buffer_index] <= 0;
                output_i[buffer_index] <= 0; output_j[buffer_index] <= 0;
                input_rows[buffer_index] <= 0; input_cols[buffer_index] <= 0;
                output_rows[buffer_index] <= 0; output_cols[buffer_index] <= 0;
                input_a_base[buffer_index] <= 0; input_bt_base[buffer_index] <= 0;
                input_c_base[buffer_index] <= 0; output_c_base[buffer_index] <= 0;
            end
        end else begin
            if (!first_fault_q && detected_fault) begin
                first_fault_q <= 1;
                first_code_q <= new_fault;
                // An idle memory loss also invalidates a prior success result.
                done <= 0;
            end
            if (job_fire) begin
                job_state <= JOB_RUN;
                done <= 0;
                m_q <= cfg_m; n_q <= cfg_n; k_q <= cfg_k; bt_base_q <= cfg_bt_base;
                a_stride_q <= cfg_a_stride; bt_stride_q <= cfg_bt_stride; c_stride_q <= cfg_c_stride;
                overlap_q <= cfg_mode[0];
                total_tiles <= 16'((((cfg_m-1)/T)+1) * (((cfg_n-1)/T)+1));
                admission_count <= 0; compute_count <= 0; retirement_count <= 0;
                admit_i <= 0; admit_j <= 0;
                admit_a_base <= cfg_a_base; admit_bt_base <= cfg_bt_base;
                admit_c_row <= cfg_c_base; admit_c_base <= cfg_c_base;
            end else begin
                case (load_state)
                    LOAD_IDLE: if (issue_allowed && free_input && admission_count < total_tiles &&
                                   (overlap_q || admission_count == retirement_count)) begin
                        load_buf_q <= free_input_id;
                        input_state[free_input_id] <= INPUT_FILLING;
                        input_tag[free_input_id] <= admission_count[14:0];
                        input_i[free_input_id] <= admit_i; input_j[free_input_id] <= admit_j;
                        input_rows[free_input_id] <= admit_rows; input_cols[free_input_id] <= admit_cols;
                        input_a_base[free_input_id] <= admit_a_base;
                        input_bt_base[free_input_id] <= admit_bt_base;
                        input_c_base[free_input_id] <= admit_c_base;
                        admission_count <= admission_count + 16'd1;
                        load_state <= LOAD_A_OFFER;
                        // Addresses advance incrementally in row-major tile order.
                        // No reduction dimension is tiled through external sums.
                        if (admission_count + 16'd1 < total_tiles) begin
                            if (admit_j + T < n_q) begin
                                admit_j <= admit_j + 32'(T);
                                admit_bt_base <= admit_bt_base + bt_stride_q * 32'(T);
                                admit_c_base <= admit_c_base + 32'(4*T);
                            end else begin
                                admit_i <= admit_i + 32'(T); admit_j <= 0;
                                admit_a_base <= admit_a_base + a_stride_q * 32'(T);
                                admit_bt_base <= bt_base_q;
                                admit_c_row <= admit_c_row + c_stride_q * 32'(T);
                                admit_c_base <= admit_c_row + c_stride_q * 32'(T);
                            end
                        end
                    end
                    LOAD_A_OFFER: if (stop_now) load_state <= LOAD_IDLE;
                                  else if (load_fire) load_state <= LOAD_A_WAIT;
                    LOAD_A_WAIT: if (load_end) load_state <= stop_now ? LOAD_IDLE : LOAD_BT_OFFER;
                    LOAD_BT_OFFER: if (stop_now) load_state <= LOAD_IDLE;
                                   else if (load_fire) load_state <= LOAD_BT_WAIT;
                    LOAD_BT_WAIT: if (load_end) begin
                        load_state <= LOAD_IDLE;
                        if (!stop_now) input_state[load_buf_q] <= INPUT_READY;
                    end
                    default: load_state <= LOAD_IDLE;
                endcase
                case (core_state)
                    CORE_IDLE: if (issue_allowed && (ready_input0 || ready_input1) && free_output) begin
                        core_input_q <= ready_input_id; core_output_q <= free_output_id;
                        input_state[ready_input_id] <= INPUT_COMPUTING;
                        output_state[free_output_id] <= OUTPUT_COMPUTING;
                        output_tag[free_output_id] <= input_tag[ready_input_id];
                        output_i[free_output_id] <= input_i[ready_input_id];
                        output_j[free_output_id] <= input_j[ready_input_id];
                        output_rows[free_output_id] <= input_rows[ready_input_id];
                        output_cols[free_output_id] <= input_cols[ready_input_id];
                        output_c_base[free_output_id] <= input_c_base[ready_input_id];
                        core_seen_busy <= 0;
                        core_state <= CORE_OFFER;
                    end
                    CORE_OFFER: if (stop_now) core_state <= CORE_IDLE;
                                else if (compute_start) begin
                        compute_count <= compute_count + 16'd1;
                        core_seen_busy <= 0;
                        core_state <= CORE_WAIT;
                    end
                    CORE_WAIT: begin
                        if (compute_busy) core_seen_busy <= 1;
                        if (core_end) begin
                            core_state <= CORE_IDLE;
                            core_seen_busy <= 0;
                            if (!stop_now) begin
                                input_state[core_input_q] <= INPUT_FREE;
                                output_state[core_output_q] <= OUTPUT_READY;
                            end
                        end
                    end
                    default: core_state <= CORE_IDLE;
                endcase
                case (store_state)
                    STORE_IDLE: if (issue_allowed && (ready_output0 || ready_output1)) begin
                        store_buf_q <= ready_output_id;
                        output_state[ready_output_id] <= OUTPUT_WRITING;
                        store_state <= STORE_OFFER;
                    end
                    STORE_OFFER: if (stop_now) store_state <= STORE_IDLE;
                                 else if (store_fire) store_state <= STORE_WAIT;
                    STORE_WAIT: if (store_end) begin
                        store_state <= STORE_IDLE;
                        if (!stop_now) begin
                            output_state[store_buf_q] <= OUTPUT_FREE;
                            retirement_count <= retirement_count + 16'd1;
                        end
                    end
                    default: store_state <= STORE_IDLE;
                endcase

                if ((job_state == JOB_RUN && stop_now) ||
                    (job_state == JOB_IDLE && !first_fault_q && detected_fault))
                    job_state <= JOB_DRAIN;
                else if (job_state == JOB_DRAIN && contexts_idle && downstream_idle) begin
                    // Accepted operations and compute have finished. Only now
                    // invalidate ready/filling/completed ownership after a fault.
                    for (buffer_index=0; buffer_index<2; buffer_index=buffer_index+1) begin
                        input_state[buffer_index] <= INPUT_FREE;
                        output_state[buffer_index] <= OUTPUT_FREE;
                    end
                    job_state <= JOB_IDLE;
                end else if (job_state == JOB_RUN && !stop_now &&
                             retirement_count == total_tiles && admission_count == total_tiles &&
                             compute_count == total_tiles && all_buffers_free && contexts_idle && downstream_idle) begin
                    // DMA busy includes its DONE handshake. Final retirement
                    // precedes this quiescent completion by at least one edge.
                    done <= 1;
                    job_state <= JOB_IDLE;
                end
            end
        end
    end

`ifndef SYNTHESIS
    initial if ((P != 4 && P != 8) || (T != 8 && T != 32) || T % P != 0 || DIM_W != $clog2(T+1))
        $fatal(1,"Unsupported scheduler geometry");
    logic held_load, held_store, held_core, previous_fault;
    logic [80:0] previous_load;
    logic [79:0] previous_store;
    logic [2*DIM_W+10:0] previous_core;
    logic [15:0] previous_code;
    wire [80:0] load_payload = {load_req_bt,load_req_buf,load_req_base,load_req_stride,load_req_rows,load_req_row_bytes};
    wire [79:0] store_payload = {store_req_buf,store_req_base,store_req_stride,store_req_rows,store_req_row_bytes};
    wire [2*DIM_W+10:0] core_payload = {compute_input_buf,compute_output_buf,compute_m,compute_n,compute_k};
    always @(posedge clk) begin
        if (rst) begin
            held_load <= 0; held_store <= 0; held_core <= 0; previous_fault <= 0; previous_code <= 0;
        end else begin
            if (detected_fault !== (new_fault != 0))
                $fatal(1,"Scheduler fault predicate differs from diagnostic payload");
            if (job_fire && (cfg_m == 0 || cfg_m > 1024 || cfg_n == 0 || cfg_n > 1024 ||
                cfg_k == 0 || cfg_k > 256 || cfg_mode > 1 ||
                cfg_a_base[5:0] != 0 || cfg_bt_base[5:0] != 0 || cfg_c_base[5:0] != 0 ||
                cfg_a_stride[5:0] != 0 || cfg_bt_stride[5:0] != 0 || cfg_c_stride[5:0] != 0 ||
                cfg_a_stride < cfg_k || cfg_bt_stride < cfg_k || {2'd0,cfg_c_stride} < {cfg_n,2'b00}))
                $fatal(1,"Scheduler received an unvalidated descriptor");
            if (held_load && !stop_now && (!load_req_valid || load_payload !== previous_load))
                $fatal(1,"Scheduler load descriptor changed while stalled");
            if (held_store && !stop_now && (!store_req_valid || store_payload !== previous_store))
                $fatal(1,"Scheduler store descriptor changed while stalled");
            if (held_core && !stop_now && (core_state != CORE_OFFER || core_payload !== previous_core))
                $fatal(1,"Scheduler compute reservation changed while waiting");
            if (previous_fault && (!fatal || fatal_code !== previous_code))
                $fatal(1,"Scheduler first fault changed after capture");
            if (busy && (retirement_count > compute_count || compute_count > admission_count || admission_count > total_tiles))
                $fatal(1,"Scheduler cursor order or bound violated");
            if (load_state != LOAD_IDLE && input_state[load_buf_q] != INPUT_FILLING)
                $fatal(1,"Scheduler load lost input ownership");
            if (core_state != CORE_IDLE && (input_state[core_input_q] != INPUT_COMPUTING ||
                                          output_state[core_output_q] != OUTPUT_COMPUTING))
                $fatal(1,"Scheduler compute lost buffer ownership");
            if (store_state != STORE_IDLE && output_state[store_buf_q] != OUTPUT_WRITING)
                $fatal(1,"Scheduler store lost output ownership");
            if (input_state[0] == INPUT_COMPUTING && input_state[1] == INPUT_COMPUTING)
                $fatal(1,"Scheduler assigned two inputs to one compute engine");
            if (output_state[0] == OUTPUT_COMPUTING && output_state[1] == OUTPUT_COMPUTING)
                $fatal(1,"Scheduler assigned two outputs to one compute engine");
            if (compute_start && ({1'b0,input_tag[core_input_q]} != compute_count ||
                                  input_tag[core_input_q] != output_tag[core_output_q]))
                $fatal(1,"Scheduler compute tag order violated");
            if (store_fire && {1'b0,output_tag[store_buf_q]} != retirement_count)
                $fatal(1,"Scheduler retirement tag order violated");
            held_load <= load_req_valid && !load_req_ready;
            held_store <= store_req_valid && !store_req_ready;
            held_core <= core_state == CORE_OFFER && !compute_ready && !stop_now;
            previous_load <= load_payload; previous_store <= store_payload; previous_core <= core_payload;
            previous_fault <= fatal; previous_code <= fatal_code;
        end
    end
`endif
endmodule
