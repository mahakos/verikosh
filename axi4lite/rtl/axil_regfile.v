// AXI4-Lite register file.
//  * NREGS x 32-bit registers. The last register is a read-only VERSION register.
//  * AW and W channels are independent: either may arrive first, each has a one-entry buffer.
//  * SLVERR on an out-of-range address or on a write to the read-only register.
//  * Protocol properties for formal live under `ifdef FORMAL (see ../formal).
module axil_regfile #(
  parameter integer        NREGS   = 16,
  parameter integer        AW      = 8,
  parameter [31:0]         VERSION = 32'h4E45_0001   // "NE" + v0.1
)(
  input  wire          aclk,
  input  wire          aresetn,
  // write address
  input  wire [AW-1:0] awaddr,
  input  wire [2:0]    awprot,
  input  wire          awvalid,
  output wire          awready,
  // write data
  input  wire [31:0]   wdata,
  input  wire [3:0]    wstrb,
  input  wire          wvalid,
  output wire          wready,
  // write response
  output reg  [1:0]    bresp,
  output reg           bvalid,
  input  wire          bready,
  // read address
  input  wire [AW-1:0] araddr,
  input  wire [2:0]    arprot,
  input  wire          arvalid,
  output wire          arready,
  // read data
  output reg  [31:0]   rdata,
  output reg  [1:0]    rresp,
  output reg           rvalid,
  input  wire          rready
);
  localparam [1:0] OKAY = 2'b00, SLVERR = 2'b10;
  localparam integer        IW     = (NREGS > 2) ? $clog2(NREGS - 1) : 1;
  localparam [AW-3:0]       RO_IDX = NREGS[AW-3:0] - 1'b1;

  reg [31:0] regs [0:NREGS-2];

  // AxPROT and the byte offset are not used by this slave
  wire unused_ok = &{1'b0, awprot, arprot, aw_q[1:0], araddr[1:0]};

  // ------------------------------------------------------------------ write path
  reg          aw_full, w_full;
  reg [AW-1:0] aw_q;
  reg [31:0]   w_q;
  reg [3:0]    strb_q;

  assign awready = !aw_full;
  assign wready  = !w_full;

  wire          aw_hs  = awvalid && awready;
  wire          w_hs   = wvalid  && wready;
  wire          b_hs   = bvalid  && bready;
  wire [AW-3:0] widx   = aw_q[AW-1:2];
  wire          commit = aw_full && w_full && (!bvalid || bready);
  wire          w_ok   = (widx < RO_IDX);
  wire [IW-1:0] wi     = widx[IW-1:0];   // only used when w_ok
  integer i;

  always @(posedge aclk) begin
    if (!aresetn) begin
      aw_full <= 1'b0;
      w_full  <= 1'b0;
      bvalid  <= 1'b0;
      bresp   <= OKAY;
      for (i = 0; i < NREGS - 1; i = i + 1) regs[i] <= 32'h0;
    end else begin
      if (aw_hs) begin aw_full <= 1'b1; aw_q <= awaddr; end
      if (w_hs)  begin w_full  <= 1'b1; w_q  <= wdata; strb_q <= wstrb; end
      if (b_hs)  bvalid <= 1'b0;
      if (commit) begin
        aw_full <= 1'b0;
        w_full  <= 1'b0;
        bvalid  <= 1'b1;
        bresp   <= w_ok ? OKAY : SLVERR;
        if (w_ok) begin
          if (strb_q[0]) regs[wi][7:0]   <= w_q[7:0];
          if (strb_q[1]) regs[wi][15:8]  <= w_q[15:8];
          if (strb_q[2]) regs[wi][23:16] <= w_q[23:16];
          if (strb_q[3]) regs[wi][31:24] <= w_q[31:24];
        end
      end
    end
  end

  // ------------------------------------------------------------------ read path
  assign arready = !rvalid;
  wire          ar_hs = arvalid && arready;
  wire [AW-3:0] ridx  = araddr[AW-1:2];
  wire [IW-1:0] ri    = ridx[IW-1:0];

  always @(posedge aclk) begin
    if (!aresetn) begin
      rvalid <= 1'b0;
      rresp  <= OKAY;
      rdata  <= 32'h0;
    end else if (ar_hs) begin
      rvalid <= 1'b1;
      if (ridx < RO_IDX)       begin rdata <= regs[ri];   rresp <= OKAY;   end
      else if (ridx == RO_IDX) begin rdata <= VERSION;    rresp <= OKAY;   end
      else                     begin rdata <= 32'h0;      rresp <= SLVERR; end
    end else if (rvalid && rready) begin
      rvalid <= 1'b0;
    end
  end

`ifdef FORMAL
  // ================================================================== formal
  reg f_past_valid = 1'b0;
  always @(posedge aclk) f_past_valid <= 1'b1;
  always @(*) if (!f_past_valid) assume (!aresetn);

  // ---- master obligations (assumed): VALID stays high and payload stable until READY
  always @(posedge aclk) if (f_past_valid && $past(aresetn) && aresetn) begin
    if ($past(awvalid && !awready)) assume (awvalid && awaddr == $past(awaddr) && awprot == $past(awprot));
    if ($past(wvalid  && !wready))  assume (wvalid  && wdata  == $past(wdata)  && wstrb  == $past(wstrb));
    if ($past(arvalid && !arready)) assume (arvalid && araddr == $past(araddr) && arprot == $past(arprot));
  end
  always @(posedge aclk) if (f_past_valid && !$past(aresetn))
    assume (!awvalid && !wvalid && !arvalid);

  // ---- slave obligations (asserted)
  // After reset every slave VALID is low.
  always @(posedge aclk) if (f_past_valid && !$past(aresetn)) begin
    assert (!bvalid);
    assert (!rvalid);
  end
  // Responses stay valid and stable until accepted.
  always @(posedge aclk) if (f_past_valid && $past(aresetn) && aresetn) begin
    if ($past(bvalid && !bready)) assert (bvalid && bresp == $past(bresp));
    if ($past(rvalid && !rready)) assert (rvalid && rresp == $past(rresp) && rdata == $past(rdata));
  end

  // Outstanding-transaction bookkeeping: a response never appears without a request.
  reg [1:0] f_aw_out, f_w_out, f_ar_out;
  always @(posedge aclk) begin
    if (!aresetn) begin
      f_aw_out <= 0; f_w_out <= 0; f_ar_out <= 0;
    end else begin
      f_aw_out <= f_aw_out + aw_hs - b_hs;
      f_w_out  <= f_w_out  + w_hs  - b_hs;
      f_ar_out <= f_ar_out + ar_hs - (rvalid && rready);
    end
  end
  always @(*) if (f_past_valid && aresetn) begin
    assert (f_aw_out <= 2 && f_w_out <= 2 && f_ar_out <= 1);
    if (bvalid) assert (f_aw_out >= 1 && f_w_out >= 1);
    if (rvalid) assert (f_ar_out == 1);
    // inductive strengthening: the counters equal the internal buffer state
    assert (f_aw_out == aw_full + bvalid);
    assert (f_w_out  == w_full  + bvalid);
    assert (f_ar_out == rvalid);
    assert (bresp == OKAY || bresp == SLVERR);
    assert (rresp == OKAY || rresp == SLVERR);
  end

  // Reads of the VERSION register always return VERSION.
  reg f_ver_rd;
  always @(posedge aclk)
    if (!aresetn)            f_ver_rd <= 1'b0;
    else if (ar_hs)          f_ver_rd <= (ridx == RO_IDX);
  always @(*) if (f_past_valid && aresetn && rvalid && f_ver_rd) assert (rdata == VERSION && rresp == OKAY);

  // ---- reachability
  always @(posedge aclk) if (f_past_valid && aresetn) begin
    cover (b_hs && bresp == OKAY);
    cover (b_hs && bresp == SLVERR);
    cover (rvalid && rready && rresp == SLVERR);
    cover (aw_full && !w_full && bvalid && !bready);   // AW ran ahead while B back-pressured
    cover (w_full && !aw_full);                         // W arrived before AW
  end
`endif
endmodule
