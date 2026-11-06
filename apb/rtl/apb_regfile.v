// APB3 register file: 8 x 32-bit registers with PSTRB, PSLVERR on an out-of-range or unaligned address.
// WAIT_MAX (0, 1 or 3) inserts 0..WAIT_MAX pseudo-random wait states per transfer (PREADY low),
// so the testbench exercises the full APB handshake. WAIT_MAX = 0 gives a zero-wait slave.
module apb_regfile #(
  parameter integer NREGS    = 8,
  parameter integer WAIT_MAX = 3
)(
  input  wire        pclk,
  input  wire        presetn,
  input  wire        psel,
  input  wire        penable,
  input  wire        pwrite,
  input  wire [7:0]  paddr,
  input  wire [31:0] pwdata,
  input  wire [3:0]  pstrb,
  output reg  [31:0] prdata,
  output wire        pready,
  output reg         pslverr
);
  localparam integer IW   = (NREGS > 1) ? $clog2(NREGS) : 1;
  localparam [1:0]   WMAX = WAIT_MAX[1:0];

  reg  [31:0]   regs [0:NREGS-1];
  wire [5:0]    idx   = paddr[7:2];
  wire          valid = (paddr[1:0] == 2'b00) && ({26'd0, idx} < NREGS);
  wire [IW-1:0] ri    = idx[IW-1:0];    // only used when valid
  wire       access = psel && penable;
  integer i;

  // wait-state generator: an 8-bit LFSR picks the number of wait states at each setup phase
  reg [7:0] lfsr;
  reg [1:0] wcnt;
  wire      setup = psel && !penable;
  assign pready = (wcnt == 2'd0);

  always @(posedge pclk or negedge presetn) begin
    if (!presetn) begin
      lfsr <= 8'hA5;
      wcnt <= 2'd0;
    end else begin
      lfsr <= {lfsr[6:0], lfsr[7] ^ lfsr[5] ^ lfsr[4] ^ lfsr[3]};
      if (setup)
        wcnt <= lfsr[1:0] & WMAX;
      else if (access && wcnt != 0)
        wcnt <= wcnt - 1'b1;
    end
  end

  always @(posedge pclk or negedge presetn) begin
    if (!presetn) begin
      for (i = 0; i < NREGS; i = i + 1) regs[i] <= 32'h0;
    end else if (access && pready && pwrite && valid) begin
      if (pstrb[0]) regs[ri][7:0]   <= pwdata[7:0];
      if (pstrb[1]) regs[ri][15:8]  <= pwdata[15:8];
      if (pstrb[2]) regs[ri][23:16] <= pwdata[23:16];
      if (pstrb[3]) regs[ri][31:24] <= pwdata[31:24];
    end
  end

  always @(*) begin
    prdata  = 32'h0;
    pslverr = 1'b0;
    if (access) begin
      pslverr = !valid;
      if (!pwrite && valid) prdata = regs[ri];
    end
  end
endmodule
