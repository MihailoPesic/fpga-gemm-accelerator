`timescale 1ns/1ps
// Cold-start integration: real UART pins, framed commands, production GEMM RTL,
// SmartConnect/MIG and the unchanged generated x16 DDR2 memory model. The high
// simulation baud reduces vendor-model runtime; it is not a board baud claim.
module tb_ddr_gemm #(
    parameter integer P=4, T=32, SIM_BAUD=10000000, READ_SLOTS=1, ENABLE_OVERLAP=0,
    parameter logic [31:0] BUILD_ID=32'd0
);
    // Match the PHY's integer clock divider (including its 115200 setting).
    localparam integer BIT_NS=(100000000/SIM_BAUD)*10;
    localparam logic [31:0] A_BASE=32'h00000fc0,
                            BT_BASE=ENABLE_OVERLAP ? 32'h00100fc0 : 32'h00001fc0,
                            C_BASE=ENABLE_OVERLAP ? 32'h00200fc0 : 32'h00002fc0;
    localparam integer STRIDE=ENABLE_OVERLAP ? 192 : 64;
    localparam logic [31:0] VERSION=ENABLE_OVERLAP ? 32'h00000200 : 32'h00000100;
    logic CLK100MHZ=0, CPU_RESETN=0, UART_TXD_IN=1;
    always #5 CLK100MHZ=!CLK100MHZ;
    wire UART_RXD_OUT;
    wire [3:0] LED;
    wire [12:0] ddr2_addr;
    wire [2:0] ddr2_ba;
    wire ddr2_ras_n, ddr2_cas_n, ddr2_we_n;
    wire [0:0] ddr2_ck_p, ddr2_ck_n, ddr2_cke, ddr2_cs_n, ddr2_odt;
    wire [1:0] ddr2_dm;
    wire [15:0] ddr2_dq;
    wire [1:0] ddr2_dqs_p, ddr2_dqs_n;
    gemm_ddr_top #(.P(P),.T(T),.BAUD(SIM_BAUD),.BUILD_ID(BUILD_ID),
                    .READ_SLOTS(READ_SLOTS), .ENABLE_OVERLAP(ENABLE_OVERLAP)) dut (.*);
    ddr2_model #(.DEBUG(0)) memory (
        .ck(ddr2_ck_p[0]), .ck_n(ddr2_ck_n[0]), .cke(ddr2_cke[0]),
        .cs_n(ddr2_cs_n[0]), .ras_n(ddr2_ras_n), .cas_n(ddr2_cas_n),
        .we_n(ddr2_we_n), .dm_rdqs(ddr2_dm), .ba(ddr2_ba), .addr(ddr2_addr),
        .dq(ddr2_dq), .dqs(ddr2_dqs_p), .dqs_n(ddr2_dqs_n), .rdqs_n(), .odt(ddr2_odt[0])
    );

    // Independent host codec. All multibyte protocol fields are little endian.
    logic [7:0] request_payload [0:255];
    logic [7:0] request_raw [0:265], request_encoded [0:269];
    logic [7:0] received_encoded [0:269], received_raw [0:265];
    logic [7:0] response_data [0:239], block_data [0:239];
    integer response_count=0, packet_count=0, request_sequence=1;
    integer response_sequence, response_opcode, response_status, response_length;
    integer host_tx_bytes=0, host_rx_bytes=0;
    integer accepted_r=0, accepted_w=0, accepted_b=0, accepted_jobs=0;
    integer successful_jobs=0, checked_outputs=0;
    integer core_edges=0, start_edge=0, final_b_edge=0;
    integer saw_upper_aw=0, saw_split_at_4k=0, saw_tail_strobe=0;
    logic calibrated_seen=0, frozen=0;
    logic [63:0] frozen_job, frozen_compute, frozen_reads, frozen_writes, frozen_bytes;
    logic [63:0] frozen_input_wait, frozen_read_stall, frozen_write_stall;
    realtime previous_core_edge=0;

    function automatic logic [31:0] crc_byte(input logic [31:0] previous,
                                               input logic [7:0] value);
        logic [31:0] state;
        integer bit_index;
        begin
            state=previous ^ {24'd0,value};
            for (bit_index=0; bit_index<8; bit_index=bit_index+1)
                state=state[0] ? (state>>1)^32'hedb88320 : state>>1;
            crc_byte=state;
        end
    endfunction

    function automatic logic [31:0] response_u32(input integer offset);
        response_u32={response_data[offset+3],response_data[offset+2],
                      response_data[offset+1],response_data[offset]};
    endfunction

    task automatic put_u16(input integer offset, input integer value);
        begin
            request_payload[offset]=value[7:0];
            request_payload[offset+1]=value[15:8];
        end
    endtask

    task automatic put_u32(input integer offset, input logic [31:0] value);
        integer b;
        begin
            for (b=0; b<4; b=b+1) request_payload[offset+b]=value>>(8*b);
        end
    endtask

    task automatic send_byte(input logic [7:0] value);
        integer b;
        begin
            // A deliberate non-clock-edge phase exercises the receiver's CDC.
            @(negedge CLK100MHZ); #2;
            UART_TXD_IN=0; #(BIT_NS);
            for (b=0; b<8; b=b+1) begin
                UART_TXD_IN=value[b]; #(BIT_NS);
            end
            UART_TXD_IN=1; #(BIT_NS);
            host_tx_bytes=host_tx_bytes+1;
        end
    endtask

    task automatic receive_byte(output logic [7:0] value);
        integer b;
        begin
            @(negedge UART_RXD_OUT);
            #(BIT_NS/2);
            if (UART_RXD_OUT !== 0) $fatal(1,"UART response start bit is invalid");
            for (b=0; b<8; b=b+1) begin
                #(BIT_NS);
                if ($isunknown(UART_RXD_OUT)) $fatal(1,"Unknown UART response data bit");
                value[b]=UART_RXD_OUT;
            end
            #(BIT_NS);
            if (UART_RXD_OUT !== 1) $fatal(1,"UART response stop bit is invalid");
            // Return before the earliest next start edge.
            #(BIT_NS/2-1);
            host_rx_bytes=host_rx_bytes+1;
        end
    endtask

    task automatic decode_response(input integer encoded_length);
        integer at, raw_length, code, b, payload_length;
        logic [31:0] checksum, received_checksum;
        begin
            at=0; raw_length=0;
            while (at<encoded_length) begin
                code=received_encoded[at]; at=at+1;
                if (code==0 || at+code-1>encoded_length)
                    $fatal(1,"Invalid COBS response code");
                for (b=1; b<code; b=b+1) begin
                    if (raw_length>=266) $fatal(1,"Oversized decoded response");
                    received_raw[raw_length]=received_encoded[at];
                    raw_length=raw_length+1; at=at+1;
                end
                if (code!=255 && at<encoded_length) begin
                    if (raw_length>=266) $fatal(1,"Oversized decoded response");
                    received_raw[raw_length]=0; raw_length=raw_length+1;
                end
            end
            if (raw_length<12 || received_raw[0]!==1)
                $fatal(1,"Malformed/version-mismatched response");
            payload_length={received_raw[5],received_raw[4]};
            if (payload_length<2 || payload_length>242 || raw_length!=10+payload_length)
                $fatal(1,"Response payload length mismatch");
            checksum=32'hffffffff;
            for (b=0; b<raw_length-4; b=b+1) checksum=crc_byte(checksum,received_raw[b]);
            checksum=checksum^32'hffffffff;
            received_checksum={received_raw[raw_length-1],received_raw[raw_length-2],
                               received_raw[raw_length-3],received_raw[raw_length-4]};
            if (checksum!==received_checksum) $fatal(1,"Response CRC32 mismatch");
            response_opcode=received_raw[1];
            response_sequence={received_raw[3],received_raw[2]};
            response_status={received_raw[7],received_raw[6]};
            response_length=payload_length-2;
            for (b=0; b<response_length; b=b+1) response_data[b]=received_raw[8+b];
            response_count=response_count+1;
        end
    endtask

    initial begin : uart_response_collector
        integer count;
        logic [7:0] value;
        count=0;
        wait (dut.core_rst===0 && dut.ddr_ready===1);
        forever begin
            receive_byte(value);
            if (value==0) begin
                if (count==0) $fatal(1,"Empty unsolicited UART response");
                decode_response(count); count=0;
            end else begin
                if (count>=270) $fatal(1,"Oversized encoded UART response");
                received_encoded[count]=value; count=count+1;
            end
        end
    end

    task automatic exchange(input integer opcode, input integer payload_length,
                            input integer expected_status);
        integer b, raw_length, encoded_length, code_at, code, wanted_response, waits;
        logic [31:0] checksum;
        begin
            if (payload_length<0 || payload_length>256) $fatal(1,"Test request length invalid");
            if (response_count!=packet_count) $fatal(1,"Unexpected extra response");
            request_raw[0]=1; request_raw[1]=opcode[7:0];
            request_raw[2]=request_sequence[7:0]; request_raw[3]=request_sequence[15:8];
            request_raw[4]=payload_length[7:0]; request_raw[5]=payload_length[15:8];
            for (b=0; b<payload_length; b=b+1) request_raw[6+b]=request_payload[b];
            checksum=32'hffffffff;
            for (b=0; b<6+payload_length; b=b+1) checksum=crc_byte(checksum,request_raw[b]);
            checksum=checksum^32'hffffffff;
            for (b=0; b<4; b=b+1) request_raw[6+payload_length+b]=checksum>>(8*b);
            raw_length=10+payload_length;
            encoded_length=1; code_at=0; code=1;
            for (b=0; b<raw_length; b=b+1) begin
                if (request_raw[b]==0) begin
                    request_encoded[code_at]=code[7:0];
                    code_at=encoded_length; encoded_length=encoded_length+1; code=1;
                end else begin
                    request_encoded[encoded_length]=request_raw[b]; encoded_length=encoded_length+1;
                    code=code+1;
                    if (code==255) begin
                        request_encoded[code_at]=8'hff;
                        code_at=encoded_length; encoded_length=encoded_length+1; code=1;
                    end
                end
            end
            request_encoded[code_at]=code[7:0];
            wanted_response=response_count+1;
            for (b=0; b<encoded_length; b=b+1) send_byte(request_encoded[b]);
            send_byte(0);
            waits=0;
            while (response_count<wanted_response && waits<3500) begin
                #(BIT_NS); waits=waits+1;
            end
            if (response_count!=wanted_response)
                $fatal(1,"UART response timeout opcode=%h sequence=%0d",opcode,request_sequence);
            if (response_opcode!=(opcode|128) || response_sequence!=request_sequence ||
                response_status!=expected_status)
                $fatal(1,"Response mismatch opcode=%h seq=%0d status=%0d expected_status=%0d",
                       response_opcode,response_sequence,response_status,expected_status);
            packet_count=packet_count+1; request_sequence=request_sequence+1;
            if (packet_count%8==0)
                $display("DDR_GEMM_PROGRESS packets=%0d opcode=%02h status=%0d time_ns=%0t",
                         packet_count,opcode,response_status,$time);
        end
    endtask

    task automatic write_register(input integer address, input logic [31:0] value,
                                  input integer status);
        begin
            put_u16(0,address); put_u32(2,value); exchange(3,6,status);
            if (response_length!=0) $fatal(1,"WRITE_REG returned unexpected data");
        end
    endtask

    task automatic read_register(input integer address, output logic [31:0] value);
        begin
            put_u16(0,address); exchange(2,2,0);
            if (response_length!=4) $fatal(1,"READ_REG response length invalid");
            value=response_u32(0);
        end
    endtask

    task automatic read_counter(input integer address, output logic [63:0] value);
        logic [31:0] lo, hi;
        begin
            read_register(address,lo); read_register(address+4,hi); value={hi,lo};
        end
    endtask

    task automatic write_memory(input logic [31:0] address, input integer length);
        integer b;
        begin
            put_u32(0,address); put_u16(4,length);
            for (b=0; b<length; b=b+1) request_payload[6+b]=block_data[b];
            exchange(5,6+length,0);
            if (response_length!=0) $fatal(1,"MEM_WRITE returned unexpected data");
        end
    endtask

    task automatic read_memory(input logic [31:0] address, input integer length);
        begin
            put_u32(0,address); put_u16(4,length); exchange(4,6,0);
            if (response_length!=length) $fatal(1,"MEM_READ response length invalid");
        end
    endtask

    function automatic integer a_value(input integer job, input integer row, input integer k);
        begin
            if (job==1 || (row==0 && k==0)) a_value=-128;
            else a_value=((row*17+k*11+3)%31)-15;
        end
    endfunction

    function automatic integer b_value(input integer job, input integer k, input integer col);
        begin
            if (job==1) b_value=-128;
            else if (k==0 && col==0) b_value=127;
            else b_value=((k*13+col*7+1)%29)-14;
        end
    endfunction

    function automatic integer expected_element(input integer job, input integer row,
                                                input integer col, input integer k_count);
        integer k, value, a_term, b_term, product;
        begin
            value=0;
            // Keep automatic function calls separate: the nested expression
            // returned X in the reduced XSim 2026.1 regression.
            for (k=0; k<k_count; k=k+1) begin
                a_term=a_value(job,row,k);
                b_term=b_value(job,k,col);
                product=a_term*b_term;
                value=value+product;
            end
            expected_element=value;
        end
    endfunction

    task automatic check_reference_oracle;
        integer row, col, value;
        integer known [0:14];
        begin
            // Independently calculated signed INT32 results for the two jobs.
            known[0]=-16185; known[1]=667; known[2]=-256;
            known[3]=606; known[4]=231; known[5]=-198;
            known[6]=-1148; known[7]=-346; known[8]=240;
            known[9]=1035; known[10]=255; known[11]=-159;
            known[12]=-936; known[13]=-74; known[14]=93;
            value=expected_element(1,0,0,1);
            if ($isunknown(value) || value!==32'sd16384)
                $fatal(1,"Scalar reference oracle is invalid: %h",value);
            for (row=0; row<5; row=row+1) begin
                for (col=0; col<3; col=col+1) begin
                    value=expected_element(2,row,col,9);
                    if ($isunknown(value) || $isunknown(known[row*3+col]) ||
                        value!==known[row*3+col])
                        $fatal(1,"Reference oracle is invalid row=%0d col=%0d expected=%0d actual=%h",
                               row,col,known[row*3+col],value);
                end
            end
            $display("DDR_GEMM_ORACLE_PASS jobs=2 outputs=16");
        end
    endtask

    task automatic run_job(input integer job, input integer m, input integer n, input integer k);
        integer row, col, index, length, offset, rounded_k, before_r, before_w, before_b;
        integer before_jobs, polls, expected, relative_byte, local_byte, expected_reads, expected_b;
        integer tile_col, columns, remaining_beats, burst_address, burst_beats, page_beats;
        logic [7:0] expected_byte;
        logic [31:0] status, value;
        logic [63:0] cycles, compute, reads, writes, valid_bytes;
        begin
            $display("DDR_GEMM_JOB_BEGIN job_id=%0d m=%0d n=%0d k=%0d time_ns=%0t",job,m,n,k,$time);
            rounded_k=((k+7)/8)*8;
            expected_reads=(m*((n+T-1)/T)+n*((m+T-1)/T))*(rounded_k/8);
            expected_b=0;
            for (row=0; row<m; row=row+1) begin
                for (tile_col=0; tile_col<n; tile_col=tile_col+T) begin
                    columns=n-tile_col;
                    if (columns>T) columns=T;
                    remaining_beats=(columns+1)/2;
                    burst_address=C_BASE+row*STRIDE+4*tile_col;
                    while (remaining_beats>0) begin
                        page_beats=(4096-(burst_address & 4095))/8;
                        burst_beats=remaining_beats;
                        if (burst_beats>16) burst_beats=16;
                        if (burst_beats>page_beats) burst_beats=page_beats;
                        expected_b=expected_b+1;
                        remaining_beats=remaining_beats-burst_beats;
                        burst_address=burst_address+8*burst_beats;
                    end
                end
            end
            for (row=0; row<m; row=row+1) begin
                for (index=0; index<rounded_k; index=index+1)
                    block_data[index]=index<k ? a_value(job,row,index) : 8'hc7;
                write_memory(A_BASE+row*STRIDE,rounded_k);
            end
            // Explicitly pack B transposed: one output column per host row.
            for (col=0; col<n; col=col+1) begin
                for (index=0; index<rounded_k; index=index+1)
                    block_data[index]=index<k ? b_value(job,index,col) : 8'hd3;
                write_memory(BT_BASE+col*STRIDE,rounded_k);
            end
            // Cover every output plus padding to the next 8-byte boundary,
            // and guards before/after the full conservative C allocation.
            // Untouched padding farther into a 64-byte stride is checked by
            // the larger portable/board suite, not by this bounded fixture.
            for (row=0; row<m; row=row+1) begin
                offset=row*STRIDE; length=((n*4+7)/8)*8;
                if (row==0) begin offset=-8; length=length+8; end
                for (index=0; index<length; index=index+1) block_data[index]=8'ha7;
                write_memory(C_BASE+offset,length);
            end
            for (index=0; index<8; index=index+1) block_data[index]=8'ha7;
            write_memory(C_BASE+m*STRIDE,8);
            write_register(16'h14,job,0);
            write_register(16'h18,m,0); write_register(16'h1c,n,0); write_register(16'h20,k,0);
            if (job==1) begin
                write_register(16'h24,A_BASE,0); write_register(16'h28,BT_BASE,0);
                write_register(16'h2c,C_BASE,0);
                write_register(16'h30,STRIDE,0); write_register(16'h34,STRIDE,0);
                write_register(16'h38,STRIDE,0);
            end
            if (ENABLE_OVERLAP) write_register(16'h3c,job==3 ? 1 : 0,0);
            before_r=accepted_r; before_w=accepted_w; before_b=accepted_b;
            before_jobs=accepted_jobs;
            write_register(16'h10,1,0);
            polls=0; status=0;
            while (!status[2] && polls<30) begin
                read_register(16'h0c,status); polls=polls+1;
                if (status[3] || status[5]) $fatal(1,"Job entered error status=%h",status);
            end
            if (status!==32'h15 || accepted_jobs!=before_jobs+1)
                $fatal(1,"Missing/duplicate successful job completion status=%h",status);
            if (accepted_r-before_r!=expected_reads || accepted_w-before_w!=m*((n+1)/2) ||
                accepted_b-before_b!=expected_b)
                $fatal(1,"Job raw AXI counts differ from independently expected row traffic");
            if (dut.core.last_job_id!==job) $fatal(1,"LAST_JOB_ID mismatch");
            read_counter(16'h80,cycles);
            read_register(16'h88,value); compute={32'd0,value};
            read_register(16'h90,value); reads={32'd0,value};
            read_register(16'h98,value); writes={32'd0,value};
            read_register(16'ha0,value); valid_bytes={32'd0,value};
            // Small fixed jobs cannot overflow these low words. Observe the
            // corresponding high words rather than spend four extra packets.
            if ({dut.core.compute_cycles[63:32],dut.core.read_beats[63:32],
                 dut.core.write_beats[63:32],dut.core.write_valid_bytes[63:32]}!==128'd0)
                $fatal(1,"Unexpected upper job counter word");
            if (cycles!==64'(final_b_edge-start_edge) || cycles==0 ||
                compute!==64'(((m+P-1)/P)*((n+P-1)/P)*(k+3*P-1)) ||
                reads!==64'(expected_reads) || writes!==64'(m*((n+1)/2)) ||
                valid_bytes!==64'(m*n*4))
                $fatal(1,"Frozen UART job counters mismatch: cycles=%0d compute=%0d R=%0d W=%0d bytes=%0d",
                       cycles,compute,reads,writes,valid_bytes);

            for (row=0; row<m; row=row+1) begin
                offset=row*STRIDE; length=((n*4+7)/8)*8;
                if (row==0) begin offset=-8; length=length+8; end
                read_memory(C_BASE+offset,length);
                for (index=0; index<length; index=index+1) begin
                    relative_byte=offset+index;
                    expected_byte=8'ha7;
                    if (relative_byte>=0 && relative_byte<m*STRIDE) begin
                        local_byte=relative_byte%STRIDE;
                        if (local_byte<n*4) begin
                            col=local_byte/4;
                            expected=expected_element(job,relative_byte/STRIDE,col,k);
                            expected_byte=expected>>(8*(local_byte%4));
                        end
                    end
                    if ($isunknown(expected_byte))
                        $fatal(1,"Unknown reference byte job=%0d row=%0d col=%0d k=%0d relative_byte=%0d expected_word=%h",
                               job,row,col,k,relative_byte,expected);
                    if (response_data[index]!==expected_byte)
                        $fatal(1,"C/guard mismatch job=%0d address=%h expected=%h actual=%h",
                               job,C_BASE+offset+index,expected_byte,response_data[index]);
                end
            end
            read_memory(C_BASE+m*STRIDE,8);
            for (index=0; index<8; index=index+1)
                if (response_data[index]!==8'ha7) $fatal(1,"Trailing C allocation guard changed");
            successful_jobs=successful_jobs+1; checked_outputs=checked_outputs+m*n;
            $display("DDR_GEMM_JOB_PASS job_id=%0d m=%0d n=%0d k=%0d outputs=%0d job_cycles=%0d compute_cycles=%0d read_beats=%0d write_beats=%0d write_bytes=%0d",
                     job,m,n,k,m*n,cycles,compute,reads,writes,valid_bytes);
        end
    endtask

    // Passive observations qualify completion/counter boundaries; stimulus and
    // all data checks still use UART. No force, hierarchical assignment or
    // direct memory-model inspection is used anywhere in this fixture.
    always @(posedge dut.core_clk) begin
        core_edges=core_edges+1;
        if (calibrated_seen && (dut.core_rst!==0 || dut.ddr_ready!==1))
            $fatal(1,"Reset or calibration loss after cold initialization");
        if (dut.ddr_ready===1 && dut.core_rst===0) calibrated_seen=1;
        if (!dut.core_rst) begin
            if (previous_core_edge!=0 &&
                ($realtime-previous_core_edge<9.999 || $realtime-previous_core_edge>10.001))
                $fatal(1,"Core clock is not 100 MHz");
            previous_core_edge=$realtime;
            if (dut.rx_valid && dut.rx_error) $fatal(1,"DUT UART receiver reported framing error");
            if (dut.m_axi_arvalid && dut.m_axi_arready) begin
                if (dut.m_axi_araddr[2:0]!=0 || dut.m_axi_arsize!=3 || dut.m_axi_arburst!=1 ||
                    dut.m_axi_arlen>15 || {1'b0,dut.m_axi_araddr[11:0]}+((dut.m_axi_arlen+1)*8)>4096 ||
                    {1'b0,dut.m_axi_araddr}+((dut.m_axi_arlen+1)*8)>33'h008000000)
                    $fatal(1,"Illegal AXI read burst");
            end
            if (dut.m_axi_awvalid && dut.m_axi_awready) begin
                if (dut.m_axi_awaddr[2:0]!=0 || dut.m_axi_awsize!=3 || dut.m_axi_awburst!=1 ||
                    dut.m_axi_awlen>15 || {1'b0,dut.m_axi_awaddr[11:0]}+((dut.m_axi_awlen+1)*8)>4096 ||
                    {1'b0,dut.m_axi_awaddr}+((dut.m_axi_awlen+1)*8)>33'h008000000)
                    $fatal(1,"Illegal AXI write burst");
                if (dut.m_axi_awaddr[3]) saw_upper_aw=1;
                if (dut.m_axi_awaddr==32'h00001000) saw_split_at_4k=1;
            end
            if (dut.m_axi_rvalid && dut.m_axi_rready) begin
                accepted_r=accepted_r+1;
                if (dut.m_axi_rresp!==0 || $isunknown(dut.m_axi_rdata))
                    $fatal(1,"Failed or unknown DDR read data");
            end
            if (dut.m_axi_wvalid && dut.m_axi_wready) begin
                accepted_w=accepted_w+1;
                if ($isunknown({dut.m_axi_wdata,dut.m_axi_wstrb,dut.m_axi_wlast}))
                    $fatal(1,"Unknown AXI write payload");
                if (dut.m_axi_wstrb==8'h0f) saw_tail_strobe=1;
            end
            if (dut.m_axi_bvalid && dut.m_axi_bready) begin
                accepted_b=accepted_b+1;
                if (dut.m_axi_bresp!==0) $fatal(1,"Failed DDR write response");
                if (dut.busy && !dut.host_busy) final_b_edge=core_edges;
            end
            #1;
            if (dut.core.job_accepted) begin
                accepted_jobs=accepted_jobs+1; start_edge=core_edges; frozen=0;
            end
            if (dut.done && !dut.busy && !frozen) begin
                if (dut.error || dut.core.reset_required || !dut.core.axi_quiescent ||
                    final_b_edge<=start_edge || dut.core.job_cycles!==64'(final_b_edge-start_edge))
                    $fatal(1,"Success preceded final write completion or timestamp is wrong");
                frozen_job=dut.core.job_cycles; frozen_compute=dut.core.compute_cycles;
                frozen_reads=dut.core.read_beats; frozen_writes=dut.core.write_beats;
                frozen_bytes=dut.core.write_valid_bytes; frozen_input_wait=dut.core.input_wait_cycles;
                frozen_read_stall=dut.core.read_stall_cycles; frozen_write_stall=dut.core.write_stall_cycles;
                frozen=1;
            end else if (frozen) begin
                if ({dut.core.job_cycles,dut.core.compute_cycles,dut.core.read_beats,dut.core.write_beats,
                     dut.core.write_valid_bytes,dut.core.input_wait_cycles,dut.core.read_stall_cycles,
                     dut.core.write_stall_cycles} !==
                    {frozen_job,frozen_compute,frozen_reads,frozen_writes,frozen_bytes,frozen_input_wait,
                     frozen_read_stall,frozen_write_stall})
                    $fatal(1,"Host traffic or rejected command modified frozen job counters");
            end
        end else previous_core_edge=0;
    end

    initial begin : host_test
        integer b, before_r, before_w, before_b, before_jobs;
        logic [31:0] value, checksum;
        if ((P!=4 && P!=8) || (T!=8 && T!=32)) $fatal(1,"Unsupported P/T configuration");
        if (SIM_BAUD!=115200 && SIM_BAUD!=1000000 && SIM_BAUD!=5000000 && SIM_BAUD!=10000000)
            $fatal(1,"Unsupported simulation baud setting");
        check_reference_oracle();
        checksum=32'hffffffff;
        for (b=0; b<9; b=b+1) checksum=crc_byte(checksum,8'h31+b);
        if ((checksum^32'hffffffff)!==32'hcbf43926) $fatal(1,"Test-side CRC32 check failed");
        $display("DDR_GEMM_SIM_CONFIG p=%0d t=%0d sim_baud=%0d build_id=%08h",
                 P,T,SIM_BAUD,BUILD_ID);
        #2000 CPU_RESETN=1;
        wait (dut.ddr_ready===1 && dut.core_rst===0);
        $display("DDR_GEMM_CALIBRATED time_ns=%0t",$time);
        repeat (10) @(negedge CLK100MHZ);
        put_u32(0,32'h57c109ab); exchange(1,4,0);
        if (response_length!=12 || response_u32(0)!==32'h57c109ab ||
            response_u32(4)!==32'h314d474e || response_u32(8)!==VERSION)
            $fatal(1,"PING identity mismatch");
        read_register(16'h50,value);
        if (value!==BUILD_ID) $fatal(1,"Bitstream BUILD_ID mismatch");
        read_register(16'h08,value);
        if (value!=={16'd256,8'(T),8'(P)}) $fatal(1,"GEOMETRY mismatch");
        read_register(16'h48,value);
        if (value!==100000000) $fatal(1,"CORE_HZ mismatch");
        // A short host command must split exactly at 4 KiB; it begins in the
        // upper 64-bit lane of a MIG word. Job uploads later replace inputs.
        for (b=0; b<32; b=b+1) block_data[b]=8'h61+b;
        write_memory(32'h00000ff8,32); read_memory(32'h00000ff8,32);
        for (b=0; b<32; b=b+1)
            if (response_data[b]!==8'(8'h61+b)) $fatal(1,"4 KiB split host memory check failed");
        run_job(1,1,1,1);
        // Rejection occurs before any DMA and preserves the last job snapshot.
        write_register(16'h18,0,0);
        before_r=accepted_r; before_w=accepted_w; before_b=accepted_b; before_jobs=accepted_jobs;
        write_register(16'h10,1,3);
        if (accepted_r!=before_r || accepted_w!=before_w || accepted_b!=before_b ||
            accepted_jobs!=before_jobs) $fatal(1,"Rejected descriptor caused memory/job effects");
        run_job(2,5,3,9);
        // A bounded two-macrotile MODE=1 case crosses the T boundary without
        // spending vendor-model runtime on a large result download. Larger
        // two-dimensional ownership/stall cases run against behavioral AXI RAM.
        if (ENABLE_OVERLAP) run_job(3,1,T+1,9);
        read_register(16'h0c,value);
        if (value!==32'h15 || dut.core.axi_quiescent!==1 || dut.host_busy!==0 ||
            dut.ddr_ready!==1 || dut.core_rst!==0 || successful_jobs!=(ENABLE_OVERLAP ? 3 : 2) ||
            checked_outputs!=(ENABLE_OVERLAP ? 16+T+1 : 16) ||
            accepted_jobs!=(ENABLE_OVERLAP ? 3 : 2) || response_count!=packet_count || !saw_upper_aw ||
            !saw_split_at_4k || !saw_tail_strobe ||
            (!ENABLE_OVERLAP && (accepted_r!=37 || accepted_w!=48 || accepted_b!=26)))
            $fatal(1,"Missing final completion, physical readiness or required transfer coverage");
        $display("DDR_GEMM_WIRE_COUNTS tx_bytes=%0d rx_bytes=%0d",host_tx_bytes,host_rx_bytes);
        $display("DDR_GEMM_FINAL_PASS jobs=%0d outputs=%0d sim_baud=%0d packets=%0d read_beats=%0d write_beats=%0d write_responses=%0d",
                 successful_jobs,checked_outputs,SIM_BAUD,packet_count,accepted_r,accepted_w,accepted_b);
        $finish;
    end
    initial begin
        #1000000;
        if (!calibrated_seen) $fatal(1,"DDR calibration exceeded 1 ms startup watchdog");
    end
    initial begin
        // Scales only wire time with baud. Calibration always gets 1 ms.
        #(1000000+BIT_NS*(ENABLE_OVERLAP ? 200000 : 80000));
        $fatal(1,"DDR GEMM vendor simulation exceeded bounded global watchdog");
    end
endmodule
