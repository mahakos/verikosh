// Synchronous FIFO, first-word fall-through, power-of-two depth.
// Push is accepted when !full, pop when !empty. Simultaneous push and pop are allowed.
// Formal properties live under `ifdef FORMAL and are proven with BMC + k-induction.
module sync_fifo #(
  parameter integer WIDTH = 8,
  parameter integer AW    = 3            // depth = 2**AW
)(
  input  wire             clk,
  input  wire             rst_n,
  input  wire             push,
  input  wire [WIDTH-1:0] wdata,
  input  wire             pop,
  output wire [WIDTH-1:0] rdata,
  output wire             full,
  output wire             empty,
  output wire [AW:0]      count
);
  localparam integer DEPTH = 1 << AW;

  reg [WIDTH-1:0] mem [0:DEPTH-1];
  reg [AW:0]      wptr, rptr;            // one extra bit tells full from empty

  wire do_push = push && !full;
  wire do_pop  = pop  && !empty;

  assign count = wptr - rptr;
  assign empty = (wptr == rptr);
  assign full  = (wptr[AW-1:0] == rptr[AW-1:0]) && (wptr[AW] != rptr[AW]);
  assign rdata = mem[rptr[AW-1:0]];

  always @(posedge clk) begin
    if (!rst_n) begin
      wptr <= 0;
      rptr <= 0;
    end else begin
      if (do_push) wptr <= wptr + 1'b1;
      if (do_pop)  rptr <= rptr + 1'b1;
    end
  end

  always @(posedge clk)
    if (do_push) mem[wptr[AW-1:0]] <= wdata;

`ifdef FORMAL
  // ---------------------------------------------------------------- assumptions
  reg f_past_valid = 1'b0;
  always @(posedge clk) f_past_valid <= 1'b1;
  always @(*) if (!f_past_valid) assume (!rst_n);

  // ---------------------------------------------------------------- invariants
  always @(*) if (f_past_valid) begin
    assert (count <= DEPTH);
    assert (empty == (count == 0));
    assert (full  == (count == DEPTH));
    assert (!(full && empty));
  end

  // ---------------------------------------------------------------- pointer behaviour
  always @(posedge clk) if (f_past_valid && $past(rst_n)) begin
    if ($past(do_push) && !$past(do_pop)) assert (count == $past(count) + 1);
    if ($past(do_pop) && !$past(do_push)) assert (count == $past(count) - 1);
    if ($past(do_push) == $past(do_pop))  assert (count == $past(count));
    if ($past(full)  && !$past(pop))      assert (full);   // no overflow
    if ($past(empty) && !$past(push))     assert (empty);  // no underflow
  end

  // ---------------------------------------------------------------- data integrity
  // Track one arbitrary value written to one arbitrary slot. While that slot is
  // live, memory must hold it, and when it reaches the head it must be what we read.
  (* anyconst *) reg [AW-1:0]    f_addr;
  (* anyconst *) reg [WIDTH-1:0] f_data;
  reg f_live;

  wire [AW-1:0] f_off = f_addr - rptr[AW-1:0];
  wire f_in_window = ({1'b0, f_off} < count);

  always @(posedge clk) begin
    if (!rst_n)
      f_live <= 1'b0;
    else begin
      if (do_push && wptr[AW-1:0] == f_addr) f_live <= (wdata == f_data);
      if (do_pop  && rptr[AW-1:0] == f_addr) f_live <= 1'b0;
    end
  end

  always @(*) if (f_past_valid && f_live) begin
    assert (f_in_window);
    assert (mem[f_addr] == f_data);
    if (rptr[AW-1:0] == f_addr) assert (rdata == f_data);
  end

  // ---------------------------------------------------------------- reachability
  always @(posedge clk) if (rst_n) begin
    cover (full);
    cover (do_push && do_pop);           // concurrent push and pop
    cover (wptr[AW] && empty);          // drained after the pointers wrapped
  end
`endif
endmodule
