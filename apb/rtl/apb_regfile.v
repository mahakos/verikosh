// APB3 register file: 8 x 32-bit registers, zero-wait-state, PSLVERR on out-of-range address.
// Used as the reference DUT for the VeriKosh APB verification components.
module apb_regfile #(
  parameter integer NREGS = 8
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
  reg [31:0] regs [0:NREGS-1];
  wire [5:0] idx   = paddr[7:2];
  wire       valid = (paddr[1:0] == 2'b00) && (idx < NREGS);
  wire       access = psel && penable;
  integer i;

  assign pready = 1'b1;

  always @(posedge pclk or negedge presetn) begin
    if (!presetn) begin
      for (i = 0; i < NREGS; i = i + 1) regs[i] <= 32'h0;
    end else if (access && pwrite && valid) begin
      if (pstrb[0]) regs[idx][7:0]   <= pwdata[7:0];
      if (pstrb[1]) regs[idx][15:8]  <= pwdata[15:8];
      if (pstrb[2]) regs[idx][23:16] <= pwdata[23:16];
      if (pstrb[3]) regs[idx][31:24] <= pwdata[31:24];
    end
  end

  always @(*) begin
    prdata  = 32'h0;
    pslverr = 1'b0;
    if (access) begin
      pslverr = !valid;
      if (!pwrite && valid) prdata = regs[idx];
    end
  end
endmodule
