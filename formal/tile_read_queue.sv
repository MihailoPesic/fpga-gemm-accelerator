// One-beat ordered descriptors: the FIFO credit, metadata and held bank port
// are real RTL. The external burst engine is a legal symbolic producer.
module tile_read_queue_formal #(parameter integer T=8) (
    input wire clk, rst, plan_valid, cmd_ready, bank_ready, terminal_ready,
    input wire [4:0] plan_row, plan_word,
    input wire [31:0] plan_addr,
    input wire data_choice, done_choice, stop_request,
    input wire [63:0] data_value,
    output wire plan_ready, cmd_valid,
    output wire [31:0] cmd_addr,
    output wire [4:0] cmd_beats,
    output wire [15:0] cmd_tag,
    output wire data_valid, data_ready, done_valid, done_ready,
    output wire bank_valid, bank_bt, bank_buf,
    output wire [$clog2(T)-1:0] bank_row,
    output wire [4:0] bank_word,
    output wire [63:0] bank_data,
    output wire terminal_valid,
    output wire [15:0] terminal_status, fault_code,
    output wire [2:0] owned,
    output wire [1:0] head, tail,
    output wire [4:0] received, delivered,
    output wire load_pending, completion_pending,
    output wire [4:0] row0,row1,row2,row3,word0,word1,word2,word3,
    output wire [4:0] beats0,beats1,beats2,beats3,
    output reg [2:0] expected_owned,
    output reg [1:0] expected_head, expected_tail,
    output reg stop,
    output reg cover_full, cover_wrap, cover_stalls, cover_stop_drain
);
    reg [4:0] rows[0:3], words[0:3];
    reg data_received, bank_delivered, terminal_received;
    reg [63:0] expected_data;
    reg [15:0] expected_status;
    reg past_valid, saw_bank_stall, saw_terminal_stall, saw_stop_work, saw_later_retire;
    reg [5:0] pushes, pops;
    wire [4:0] observed_beats[0:3];
    assign observed_beats[0]=beats0;
    assign observed_beats[1]=beats1;
    assign observed_beats[2]=beats2;
    assign observed_beats[3]=beats3;
    wire push = cmd_valid && cmd_ready;
    wire bank_fire = bank_valid && bank_ready;
    wire pop = terminal_valid && terminal_ready;
    assign data_valid = data_choice && expected_owned != 0 && !data_received && !terminal_received;
    assign done_valid = done_choice && expected_owned != 0 && data_received &&
                        (stop || bank_delivered) && !terminal_received;
    wire data_fire = data_valid && data_ready;
    wire done_fire = done_valid && done_ready;
    // Geometry is set on the original DUT by the runner before observation
    // ports are exposed. A second parameter derivation would discard them.
    gemm_tile_dma_read_queue dut (
        .clk(clk),.rst(rst),.stop(stop),.stop_status(16'd7),.saved_bt(1'b1),.saved_buf(1'b1),
        .plan_valid(plan_valid),.plan_ready(plan_ready),.plan_addr(plan_addr),.plan_beats(5'd1),
        .plan_row(plan_row),.plan_word(plan_word),.plan_last(1'b0),
        .complete_valid(terminal_valid),.complete_ready(terminal_ready),.complete_status(terminal_status),.fault_code(fault_code),
        .load_valid(bank_valid),.load_ready(bank_ready),.load_bt(bank_bt),.load_buf(bank_buf),
        .load_q(bank_row),.load_word(bank_word),.load_data(bank_data),
        .rd_cmd_valid(cmd_valid),.rd_cmd_ready(cmd_ready),.rd_cmd_addr(cmd_addr),.rd_cmd_beats(cmd_beats),.rd_cmd_tag(cmd_tag),
        .rd_data_valid(data_valid),.rd_data_ready(data_ready),.rd_data(data_value),.rd_data_index(4'd0),
        .rd_data_last(1'b1),.rd_data_tag({14'd0,expected_head}),
        .rd_done_valid(done_valid),.rd_done_ready(done_ready),.rd_done_status(stop ? 16'd7 : 16'd0),
        .rd_done_tag({14'd0,expected_head}),.obs_owned(owned),.obs_head(head),.obs_tail(tail),
        .obs_received(received),.obs_delivered(delivered),.obs_load_pending(load_pending),
        .obs_completion_pending(completion_pending),
        .obs_row0(row0),.obs_row1(row1),.obs_row2(row2),.obs_row3(row3),
        .obs_word0(word0),.obs_word1(word1),.obs_word2(word2),.obs_word3(word3),
        .obs_beats0(beats0),.obs_beats1(beats1),.obs_beats2(beats2),.obs_beats3(beats3)
    );
    integer i;
    initial begin
        expected_owned=0; expected_head=0; expected_tail=0; stop=0; past_valid=0;
        data_received=0; bank_delivered=0; terminal_received=0; expected_data=0; expected_status=0;
        pushes=0; pops=0; saw_bank_stall=0; saw_terminal_stall=0; saw_stop_work=0; saw_later_retire=0;
        cover_full=0; cover_wrap=0; cover_stalls=0; cover_stop_drain=0;
        for(i=0;i<4;i=i+1) begin rows[i]=0; words[i]=0; end
    end
    always @(posedge clk) begin
        if (rst) begin
            expected_owned<=0; expected_head<=0; expected_tail<=0; stop<=0; past_valid<=0;
            data_received<=0; bank_delivered<=0; terminal_received<=0; expected_data<=0; expected_status<=0;
            pushes<=0; pops<=0; saw_bank_stall<=0; saw_terminal_stall<=0; saw_stop_work<=0; saw_later_retire<=0;
            cover_full<=0; cover_wrap<=0; cover_stalls<=0; cover_stop_drain<=0;
            for(i=0;i<4;i=i+1) begin rows[i]<=0; words[i]<=0; end
        end else begin
            past_valid<=1;
            // The planner supplies valid bank metadata and a stable offer. The
            // burst source preserves a stalled word; no fairness is imposed.
            if (plan_valid) assume(plan_row < T);
            if (past_valid && $past(plan_valid && !plan_ready) && !stop) begin
                assume(plan_valid);
                assume({plan_addr,plan_row,plan_word} == $past({plan_addr,plan_row,plan_word}));
            end
            if (past_valid && $past(data_valid && !data_ready)) begin
                assume(data_valid); assume(data_value == $past(data_value));
            end
            if (stop_request) stop<=1;
            expected_owned <= expected_owned + push - pop;
            if (push) begin
                rows[expected_tail]<=plan_row; words[expected_tail]<=plan_word;
                expected_tail<=expected_tail+1'b1; pushes<=pushes+1'b1;
            end
            if (data_fire) begin data_received<=1; expected_data<=data_value; end
            if (bank_fire) bank_delivered<=1;
            if (done_fire) begin terminal_received<=1; expected_status<=stop ? 7 : 0; end
            if (pop) begin
                expected_head<=expected_head+1'b1; pops<=pops+1'b1;
                data_received<=0; bank_delivered<=0; terminal_received<=0;
            end
            assert(owned == expected_owned && owned <= 4);
            assert(head == expected_head && tail == expected_tail);
            assert(tail == 2'(head + owned));
            assert({row3,row2,row1,row0} == {rows[3],rows[2],rows[1],rows[0]});
            assert({word3,word2,word1,word0} == {words[3],words[2],words[1],words[0]});
            assert(row0 < T && row1 < T && row2 < T && row3 < T);
            for(i=0;i<4;i=i+1) begin
                assert(observed_beats[i] <= 1);
                if (i < owned) assert(observed_beats[2'(head+i)] == 1);
            end
            assert(received == (data_received ? 1 : 0));
            assert(delivered == (bank_delivered ? 1 : 0));
            assert(completion_pending == terminal_received);
            assert(!bank_delivered || data_received);
            assert(!terminal_received || data_received);
            assert(expected_owned != 0 || (!data_received && !bank_delivered && !terminal_received && !load_pending));
            assert(!push || owned < 4);
            assert(!pop || owned != 0);
            assert((plan_valid && plan_ready) == (cmd_valid && cmd_ready));
            if (cmd_valid) begin assert(cmd_tag == {14'd0,expected_tail}); assert(cmd_beats == 1); assert(cmd_addr == plan_addr); end
            assert(!stop || !cmd_valid);
            if (bank_valid) begin
                assert(expected_owned != 0 && data_received && !bank_delivered && !terminal_received);
                assert(bank_row == rows[expected_head][$clog2(T)-1:0] && bank_word == words[expected_head]);
                assert(bank_data == expected_data && bank_bt && bank_buf);
            end
            if (terminal_valid) begin
                assert(expected_owned != 0 && terminal_received && !bank_valid);
                assert(terminal_status == expected_status);
            end
            assert(fault_code == (done_fire && stop ? 7 : 0));
            if (past_valid) begin
                if ($past(bank_valid && !bank_ready)) begin
                    assert(bank_valid);
                    assert({bank_bt,bank_buf,bank_row,bank_word,bank_data} ==
                           $past({bank_bt,bank_buf,bank_row,bank_word,bank_data}));
                end
                if ($past(terminal_valid && !terminal_ready)) begin
                    assert(terminal_valid); assert(terminal_status == $past(terminal_status));
                end
            end
            if (owned == 4) cover_full<=1;
            if (pushes >= 5 && pops >= 5 && owned == 0 && !stop) cover_wrap<=1;
            if (bank_valid && !bank_ready) saw_bank_stall<=1;
            if (terminal_valid && !terminal_ready) saw_terminal_stall<=1;
            if (saw_bank_stall && saw_terminal_stall && pops != 0) cover_stalls<=1;
            if (!stop && stop_request && owned != 0 && !pop) saw_stop_work<=1;
            if (saw_stop_work && pop) saw_later_retire<=1;
            if (saw_later_retire && stop && owned == 0 && !bank_valid && !terminal_valid) cover_stop_drain<=1;
        end
    end
endmodule
