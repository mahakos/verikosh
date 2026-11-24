// Dual-clock (asynchronous) FIFO for clock-domain crossing, first-word fall-through, depth 2**AW.
//
//  * Binary pointers address the memory; Gray-coded copies cross the clock boundary, so a pointer seen
//    mid-change in the other domain is off by at most one position (never a garbage value).
//  * Each Gray pointer crosses through a SYNC_STAGES-flop synchroniser (default 2).
//  * wfull and rempty are registered and pessimistic: full may stay high, and empty may stay high,
//    for a few cycles after the other side moved, but full is never low while the FIFO is full
//    and empty is never low while it is empty.
//  * Each domain has its own synchronous active-low reset; assert both together.
// Formal properties (multi-clock) are at the end of the file, see ../formal.
module async_fifo #(
  parameter integer WIDTH       = 8,
  parameter integer AW          = 3,
  parameter integer SYNC_STAGES = 2
)(
  // write domain
  input  wire             wclk,
  input  wire             wrst_n,
  input  wire             winc,
  input  wire [WIDTH-1:0] wdata,
  output reg              wfull,
  // read domain
  input  wire             rclk,
  input  wire             rrst_n,
  input  wire             rinc,
  output wire [WIDTH-1:0] rdata,
  output reg              rempty
);
  localparam integer DEPTH = 1 << AW;

  reg [WIDTH-1:0] mem [0:DEPTH-1];

  // ------------------------------------------------------------------ write domain
  reg  [AW:0] wbin, wgray;
  reg  [AW:0] wsync [0:SYNC_STAGES-1];          // read pointer (Gray) synchronised into wclk
  wire [AW:0] wq_rgray  = wsync[SYNC_STAGES-1];
  wire        wpush     = winc && !wfull;
  wire [AW:0] wbin_nxt  = wbin + {{AW{1'b0}}, wpush};
  wire [AW:0] wgray_nxt = (wbin_nxt >> 1) ^ wbin_nxt;
  // full: next write pointer equals the read pointer with the two MSBs inverted (Gray domain)
  wire        wfull_nxt = (wgray_nxt == {~wq_rgray[AW:AW-1], wq_rgray[AW-2:0]});

  integer i;
  always @(posedge wclk) begin
    if (!wrst_n) begin
      wbin  <= 0;
      wgray <= 0;
      wfull <= 1'b0;
      for (i = 0; i < SYNC_STAGES; i = i + 1) wsync[i] <= 0;
    end else begin
      wbin  <= wbin_nxt;
      wgray <= wgray_nxt;
      wfull <= wfull_nxt;
      wsync[0] <= rgray;
      for (i = 1; i < SYNC_STAGES; i = i + 1) wsync[i] <= wsync[i-1];
    end
  end

  always @(posedge wclk)
    if (wpush) mem[wbin[AW-1:0]] <= wdata;

  // ------------------------------------------------------------------ read domain
  reg  [AW:0] rbin, rgray;
  reg  [AW:0] rsync [0:SYNC_STAGES-1];          // write pointer (Gray) synchronised into rclk
  wire [AW:0] rq_wgray   = rsync[SYNC_STAGES-1];
  wire        rpop       = rinc && !rempty;
  wire [AW:0] rbin_nxt   = rbin + {{AW{1'b0}}, rpop};
  wire [AW:0] rgray_nxt  = (rbin_nxt >> 1) ^ rbin_nxt;
  wire        rempty_nxt = (rgray_nxt == rq_wgray);

  integer j;
  always @(posedge rclk) begin
    if (!rrst_n) begin
      rbin   <= 0;
      rgray  <= 0;
      rempty <= 1'b1;
      for (j = 0; j < SYNC_STAGES; j = j + 1) rsync[j] <= 0;
    end else begin
      rbin   <= rbin_nxt;
      rgray  <= rgray_nxt;
      rempty <= rempty_nxt;
      rsync[0] <= wgray;
      for (j = 1; j < SYNC_STAGES; j = j + 1) rsync[j] <= rsync[j-1];
    end
  end

  assign rdata = mem[rbin[AW-1:0]];

`ifdef FORMAL
  // Multi-clock proof: wclk and rclk are free inputs sampled on a global formal clock, so the solver
  // tries every interleaving and phase of the two clocks' edges. Both resets are one signal.
  localparam [AW:0] DEPTH_P = DEPTH;

  function [AW:0] g2b(input [AW:0] g);
    integer k;
    begin
      g2b[AW] = g[AW];
      for (k = AW - 1; k >= 0; k = k - 1) g2b[k] = g2b[k+1] ^ g[k];
    end
  endfunction

  // ---------------------------------------------------------------- environment
  reg f_past = 1'b0, f_wseen = 1'b0, f_rseen = 1'b0, f_released = 1'b0;
  always @($global_clock) begin
    f_past <= 1'b1;
    if (wrst_n) f_released <= 1'b1;
  end
  always @(posedge wclk) if (!wrst_n) f_wseen <= 1'b1;
  always @(posedge rclk) if (!rrst_n) f_rseen <= 1'b1;
  wire f_on = f_wseen && f_rseen && wrst_n;          // reset seen by both domains and released

  always @(*) begin
    assume (wrst_n == rrst_n);
    if (!(f_wseen && f_rseen)) assume (!wrst_n);      // hold reset until both domains saw an edge
    if (f_released) assume (wrst_n);                  // one reset only
  end

  // the pointer each registered flag was computed from (one stage older than the last sync stage)
  reg [AW:0] f_wsrc, f_rsrc;
  reg        f_written;                               // see data integrity below
  always @(posedge wclk) f_wsrc <= wrst_n ? wq_rgray : {(AW+1){1'b0}};
  always @(posedge rclk) f_rsrc <= rrst_n ? rq_wgray : {(AW+1){1'b0}};

  wire [AW:0] f_occ = wbin - rbin;                    // true occupancy

  // while reset is held after an edge, each domain sits in its reset state (needed for induction)
  integer f_k;
  always @(*) begin
    if (f_wseen && !wrst_n) begin
      assert (wbin == 0 && wgray == 0 && !wfull && f_wsrc == 0 && !f_written);
      for (f_k = 0; f_k < SYNC_STAGES; f_k = f_k + 1) assert (wsync[f_k] == 0);
    end
    if (f_rseen && !rrst_n) begin
      assert (rbin == 0 && rgray == 0 && rempty && f_rsrc == 0);
      for (f_k = 0; f_k < SYNC_STAGES; f_k = f_k + 1) assert (rsync[f_k] == 0);
    end
  end

  // ---------------------------------------------------------------- safety
  always @(*) if (f_on) begin
    assert (f_occ <= DEPTH_P);                        // never overflows (and never underflows)
    assert (wgray == ((wbin >> 1) ^ wbin));           // crossing pointers are Gray code
    assert (rgray == ((rbin >> 1) ^ rbin));
    assert (wfull  == ((wbin - g2b(f_wsrc)) == DEPTH_P));
    assert (rempty == (g2b(f_rsrc) == rbin));
  end

  // Every synchroniser stage holds a pointer value the other side really had, and older stages
  // hold older values. This is what makes the flags pessimistic, and it is what k-induction needs.
  genvar gs;
  generate for (gs = 0; gs <= SYNC_STAGES; gs = gs + 1) begin : f_sync
    wire [AW:0] rq = g2b(gs == SYNC_STAGES ? f_wsrc : wsync[gs]);   // read pointer copies (wclk)
    wire [AW:0] wq = g2b(gs == SYNC_STAGES ? f_rsrc : rsync[gs]);   // write pointer copies (rclk)
    always @(*) if (f_on) begin
      assert ((wbin - rq) <= DEPTH_P);
      assert ((rbin - rq) <= (wbin - rq));
      assert ((wbin - wq) <= f_occ);
    end
    if (gs > 0) begin : f_order
      wire [AW:0] rq_newer = g2b(wsync[gs-1]);
      wire [AW:0] wq_newer = g2b(rsync[gs-1]);
      always @(*) if (f_on) begin
        assert ((rbin - rq) >= (rbin - rq_newer));
        assert ((wbin - wq) >= (wbin - wq_newer));
      end
    end
  end endgenerate

  // a Gray pointer never changes by more than one bit between two samples, whatever the clocks do
  always @($global_clock) if (f_past && f_on && $past(f_on)) begin
    assert ($countones(wgray ^ $past(wgray)) <= 1);
    assert ($countones(rgray ^ $past(rgray)) <= 1);
  end

  // ---------------------------------------------------------------- data integrity
  // Follow the word written at one arbitrary pointer value until it is read.
  (* anyconst *) reg [AW:0] f_ptr;
  reg [WIDTH-1:0] f_data;
  always @(posedge wclk)
    if (!wrst_n) f_written <= 1'b0;
    else if (wpush && wbin == f_ptr) begin
      f_written <= 1'b1;
      f_data    <= wdata;
    end
  wire f_live = f_written && ((f_ptr - rbin) < f_occ);   // written and not yet read
  always @(*) if (f_on) begin
    if (f_live) assert (mem[f_ptr[AW-1:0]] == f_data);
    if (f_live && rbin == f_ptr) assert (rdata == f_data);
    if (!f_written) assert ((f_ptr - rbin) >= f_occ);    // nothing live at f_ptr before it is written
  end

  // ---------------------------------------------------------------- reachability
  always @(*) if (f_on) begin
    cover (wfull);
    cover (f_live && rbin == f_ptr && !rempty);
    cover (rbin[AW] && wfull);                           // full again after the pointers wrapped
  end
`endif
endmodule
