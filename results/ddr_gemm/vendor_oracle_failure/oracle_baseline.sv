`timescale 1ns/1ps
module oracle_probe;
localparam integer STRIDE=64;
logic [7:0] block_data[0:239];
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
        integer k, value;
        begin
            value=0;
            for (k=0; k<k_count; k=k+1) value=value+a_value(job,row,k)*b_value(job,k,col);
            expected_element=value;
        end
    endfunction

    task automatic run_job(input integer job, input integer m, input integer n, input integer k);
        integer row, col, index, length, offset, rounded_k, before_r, before_w, before_b;
        integer before_jobs, polls, expected, relative_byte, local_byte;
        logic [7:0] expected_byte;
        logic [31:0] status, value;
        logic [63:0] cycles, compute, reads, writes, valid_bytes;

        begin
            rounded_k=((k+7)/8)*8;
            for (row=0; row<m; row=row+1) begin
                for (index=0; index<rounded_k; index=index+1)
                    block_data[index]=index<k ? a_value(job,row,index) : 8'hc7;
                #10;
            end
            for (col=0; col<n; col=col+1) begin
                for (index=0; index<rounded_k; index=index+1)
                    block_data[index]=index<k ? b_value(job,index,col) : 8'hd3;
                #10;
            end
            #100;
            for (row=0; row<m; row=row+1) begin
                offset=row*STRIDE; length=((n*4+7)/8)*8;
                if (row==0) begin offset=-8; length=length+8; end
                #10;
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

                    $display("ORACLE job=%0d row=%0d col=%0d k=%0d offset=%0d index=%0d relative=%0d local=%0d expected=%h byte=%h", job,row,col,k,offset,index,relative_byte,local_byte,expected,expected_byte);
                    if ($isunknown(expected_byte)) $fatal(1,"Unknown expected byte");
                    if (job==2 && row==0 && index==8 && expected_byte!==8'hc7) $fatal(1,"Wrong first byte");
                end
            end
        end
    endtask
initial begin : direct_checks
    integer kk,rr,cc,got,a_term,b_term;
    integer known[0:14];
    known[0]=-16185;known[1]=667;known[2]=-256;
    known[3]=606;known[4]=231;known[5]=-198;
    known[6]=-1148;known[7]=-346;known[8]=240;
    known[9]=1035;known[10]=255;known[11]=-159;
    known[12]=-936;known[13]=-74;known[14]=93;
    for(kk=0;kk<9;kk=kk+1) begin
        a_term=a_value(2,0,kk); b_term=b_value(2,kk,0);
        $display("OPERANDS k=%0d a=%0d b=%0d product=%0d",kk,a_term,b_term,a_term*b_term);
    end
    $display("DIRECT job1=%h job2=%h",expected_element(1,0,0,1),expected_element(2,0,0,9));
    for(rr=0;rr<5;rr=rr+1) for(cc=0;cc<3;cc=cc+1) begin
        got=expected_element(2,rr,cc,9);
        if(got!==known[rr*3+cc]) $fatal(1,"Literal oracle mismatch row=%0d col=%0d got=%h expected=%h",rr,cc,got,known[rr*3+cc]);
    end
    run_job(1,1,1,1);
    #100;
    run_job(2,5,3,9);
    $display("ORACLE_PROBE_PASS");
    $finish;
end
endmodule
