// Two-macrotile ownership proof. The runner exposes existing DUT registers as
// observation-only ports after lowering memories; it does not cut any driver.
module tile_scheduler_formal #(
    parameter integer MODE = 0
) (
    input wire clk, rst, job_request,
    input wire load_ready_choice, store_ready_choice, core_ready_choice,
    input wire load_finish, store_finish, core_finish,
    input wire load_failure, store_failure, mem_fatal, compute_error,
    output wire job_ready, busy, done, fatal,
    output wire [15:0] fatal_code,
    output wire load_valid, load_ready, load_bt, load_buf,
    output wire [31:0] load_base, load_stride,
    output wire [5:0] load_rows,
    output wire [8:0] load_bytes,
    output wire store_valid, store_ready, store_buf,
    output wire [31:0] store_base, store_stride,
    output wire [5:0] store_rows,
    output wire [8:0] store_bytes,
    output wire core_start, core_complete, core_busy,
    output wire core_input, core_output,
    output wire [3:0] core_m, core_n,
    output wire [8:0] core_k,
    output wire [1:0] input0, input1, output0, output1,
    output wire [2:0] load_state,
    output wire [1:0] store_state, core_state, job_state,
    output wire [15:0] admitted, computed, retired, total,
    output wire [14:0] input_tag0, input_tag1, output_tag0, output_tag1,
    output reg load_pending, store_pending, core_pending,
    output wire load_end, store_end, core_done,
    output reg cover_success, cover_overlap, cover_held, cover_fault_drain
);
    reg accepted, past_valid, core_seen;
    reg [2:0] loads_accepted;
    reg [1:0] starts_accepted, stores_accepted, stores_finished;
    reg held_load, held_store, fault_with_work, fault_followed_by_completion;
    wire load_done_ready, store_done_ready, core_ready, detected_fault;
    wire [15:0] detected_code;
    wire job_valid = job_request && !accepted;
    assign load_ready = load_ready_choice && !load_pending;
    assign store_ready = store_ready_choice && !store_pending;
    assign core_ready = core_ready_choice && !core_pending;
    assign load_end = load_pending && load_finish;
    assign store_end = store_pending && store_finish;
    assign core_done = core_pending && core_seen && core_finish;
    assign core_busy = core_pending && !core_done;
    wire load_fire = load_valid && load_ready;
    wire store_fire = store_valid && store_ready;
    wire [80:0] load_payload = {load_bt,load_buf,load_base,load_stride,load_rows,load_bytes};
    wire [79:0] store_payload = {store_buf,store_base,store_stride,store_rows,store_bytes};
    wire [14:0] selected_input_tag = core_input ? input_tag1 : input_tag0;
    wire [14:0] selected_output_tag = core_output ? output_tag1 : output_tag0;
    wire [14:0] store_tag = store_buf ? output_tag1 : output_tag0;
    wire [1:0] load_owned = load_buf ? input1 : input0;
    wire [1:0] core_input_owned = core_input ? input1 : input0;
    wire [1:0] core_output_owned = core_output ? output1 : output0;
    wire [1:0] store_owned = store_buf ? output1 : output0;
    gemm_tile_scheduler dut (
        .clk(clk),.rst(rst),.job_valid(job_valid),.job_ready(job_ready),
        .cfg_m(32'd1),.cfg_n(32'd9),.cfg_k(32'd1),
        .cfg_a_base(32'h1000),.cfg_bt_base(32'h2000),.cfg_c_base(32'h3000),
        .cfg_a_stride(32'd64),.cfg_bt_stride(32'd64),.cfg_c_stride(32'd64),.cfg_mode(32'(MODE)),
        .busy(busy),.done(done),.fatal(fatal),.fatal_code(fatal_code),
        .mem_fatal(mem_fatal),.mem_fatal_code(16'd7),
        .load_busy(load_pending),.store_busy(store_pending),
        .burst_local_idle(!load_pending && !store_pending),
        .axi_quiescent(!load_pending && !store_pending),
        .load_req_valid(load_valid),.load_req_ready(load_ready),.load_req_bt(load_bt),.load_req_buf(load_buf),
        .load_req_base(load_base),.load_req_stride(load_stride),.load_req_rows(load_rows),.load_req_row_bytes(load_bytes),
        .load_done_valid(load_end),.load_done_ready(load_done_ready),.load_done_status(load_failure ? 16'd7 : 16'd0),
        .store_req_valid(store_valid),.store_req_ready(store_ready),.store_req_buf(store_buf),
        .store_req_base(store_base),.store_req_stride(store_stride),.store_req_rows(store_rows),.store_req_row_bytes(store_bytes),
        .store_done_valid(store_end),.store_done_ready(store_done_ready),.store_done_status(store_failure ? 16'd7 : 16'd0),
        .compute_start(core_start),.compute_ready(core_ready),.compute_busy(core_busy),.compute_done(core_done),
        .compute_error(compute_error),.compute_input_buf(core_input),.compute_output_buf(core_output),
        .compute_m(core_m),.compute_n(core_n),.compute_k(core_k),.compute_complete(core_complete),
        .detected_fault(detected_fault),.detected_fault_code(detected_code),
        .obs_input0(input0),.obs_input1(input1),.obs_output0(output0),.obs_output1(output1),
        .obs_load_state(load_state),.obs_store_state(store_state),.obs_core_state(core_state),.obs_job_state(job_state),
        .obs_admitted(admitted),.obs_computed(computed),.obs_retired(retired),.obs_total(total),
        .obs_input_tag0(input_tag0),.obs_input_tag1(input_tag1),
        .obs_output_tag0(output_tag0),.obs_output_tag1(output_tag1)
    );
    initial begin
        past_valid=0; accepted=0; load_pending=0; store_pending=0; core_pending=0; core_seen=0;
        loads_accepted=0; starts_accepted=0; stores_accepted=0; stores_finished=0;
        held_load=0; held_store=0; fault_with_work=0; fault_followed_by_completion=0;
        cover_success=0; cover_overlap=0; cover_held=0; cover_fault_drain=0;
    end
    always @(posedge clk) begin
        if (rst) begin
            past_valid<=0; accepted<=0; load_pending<=0; store_pending<=0; core_pending<=0; core_seen<=0;
            loads_accepted<=0; starts_accepted<=0; stores_accepted<=0; stores_finished<=0;
            held_load<=0; held_store<=0; fault_with_work<=0; fault_followed_by_completion<=0;
            cover_success<=0; cover_overlap<=0; cover_held<=0; cover_fault_drain<=0;
        end else begin
            past_valid<=1;
            if (job_valid && job_ready) accepted<=1;
            if (load_fire) begin load_pending<=1; loads_accepted<=loads_accepted+1'b1; end
            if (load_end) load_pending<=0;
            if (store_fire) begin store_pending<=1; stores_accepted<=stores_accepted+1'b1; end
            if (store_end) begin store_pending<=0; stores_finished<=stores_finished+1'b1; end
            if (core_start) begin core_pending<=1; core_seen<=0; starts_accepted<=starts_accepted+1'b1; end
            if (core_busy) core_seen<=1;
            if (core_done) begin core_pending<=0; core_seen<=0; end
            assert(retired <= computed && computed <= admitted && admitted <= total);
            assert(total <= 2);
            assert(!(input0 == 3 && input1 == 3));
            assert(!(output0 == 1 && output1 == 1));
            if (load_state != 0) assert(load_owned == 1);
            if (core_state != 0) begin assert(core_input_owned == 3); assert(core_output_owned == 1); end
            if (store_state != 0) assert(store_owned == 3);
            if (load_pending) assert(load_state == 2 || load_state == 4);
            if (store_pending) assert(store_state == 2);
            if (core_pending) assert(core_state == 2);
            if (load_valid) begin
                assert(load_owned == 1 && load_stride == 64 && load_bytes == 1);
                assert(load_bt == loads_accepted[0]);
                assert(load_base == (load_bt ? (loads_accepted < 2 ? 32'h2000 : 32'h2200) : 32'h1000));
                assert(load_rows == (load_bt && loads_accepted >= 2 ? 1 : load_bt ? 8 : 1));
            end
            if (core_start) begin
                assert(selected_input_tag == starts_accepted && selected_output_tag == selected_input_tag);
                assert(core_m == 1 && core_n == (starts_accepted == 0 ? 8 : 1) && core_k == 1);
            end
            if (store_valid) begin
                assert(store_tag == stores_accepted);
                assert(store_base == (stores_accepted == 0 ? 32'h3000 : 32'h3020));
                assert(store_stride == 64 && store_rows == 1 && store_bytes == (stores_accepted == 0 ? 32 : 4));
            end
            if (MODE == 0) begin
                assert(admitted <= retired + 1);
                assert(!(load_state != 0 && (core_state != 0 || store_state != 0)));
                assert(!(core_state != 0 && store_state != 0));
            end
            if (fatal || detected_fault) assert(!load_valid && !store_valid && !core_start && !job_ready);
            if (done) assert(!fatal && !busy && !load_pending && !store_pending && !core_pending && stores_finished == 2);
            if (past_valid) begin
                if ($past(load_valid && !load_ready) && !fatal && !detected_fault)
                    begin assert(load_valid); assert(load_payload == $past(load_payload)); end
                if ($past(store_valid && !store_ready) && !fatal && !detected_fault)
                    begin assert(store_valid); assert(store_payload == $past(store_payload)); end
                if ($past(fatal)) begin assert(fatal); assert(fatal_code == $past(fatal_code)); end
                // A fault cannot free storage until every accepted owner drains.
                if ($past(fatal && busy) && !busy) assert(!load_pending && !store_pending && !core_pending);
                if ($past(input0 != 0) && input0 == 0)
                    assert($past(core_complete && !core_input && !fatal && !detected_fault) || (fatal && !busy));
                if ($past(input1 != 0) && input1 == 0)
                    assert($past(core_complete && core_input && !fatal && !detected_fault) || (fatal && !busy));
                if ($past(output0 != 0) && output0 == 0)
                    assert($past(store_end && !store_buf && !fatal && !detected_fault) || (fatal && !busy));
                if ($past(output1 != 0) && output1 == 0)
                    assert($past(store_end && store_buf && !fatal && !detected_fault) || (fatal && !busy));
            end
            if (load_valid && !load_ready) held_load<=1;
            if (store_valid && !store_ready) held_store<=1;
            if (held_load && held_store) cover_held<=1;
            if (MODE == 1 && load_pending && core_pending && load_buf != core_input) cover_overlap<=1;
            if (done && stores_finished == 2) cover_success<=1;
            if (!fatal && detected_fault && (load_pending || store_pending || core_pending) &&
                !(load_end || store_end || core_complete)) fault_with_work<=1;
            if (fault_with_work && (load_end || store_end || core_complete)) fault_followed_by_completion<=1;
            if (fault_followed_by_completion && fatal && !busy && !done &&
                input0 == 0 && input1 == 0 && output0 == 0 && output1 == 0) cover_fault_drain<=1;
        end
    end
endmodule
