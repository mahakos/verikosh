// SPI master: all four modes (CPOL/CPHA), MSB- or LSB-first, 8-bit frames, runtime clock divider.
//  * div       SCLK half-period in clk cycles (>= 1). SCLK = clk / (2*div).
//  * Each frame: CS_N falls, div+1 cycles of setup, 16 SCLK edges, div cycles of hold,
//    CS_N rises for at least div cycles before the next frame.
//  * Configuration inputs must be stable while a frame is in flight.
module spi_master (
  input  wire       clk,
  input  wire       rst_n,
  input  wire       cpol,
  input  wire       cpha,
  input  wire       lsb_first,
  input  wire [7:0] div,
  // byte interface
  input  wire [7:0] tx_data,
  input  wire       tx_valid,
  output wire       tx_ready,
  output reg  [7:0] rx_data,
  output reg        rx_valid,
  // SPI pins
  output reg        sclk,
  output reg        mosi,
  input  wire       miso,
  output reg        cs_n
);
  localparam [2:0] IDLE = 3'd0, SETUP = 3'd1, XFER = 3'd2, HOLD = 3'd3, GAP = 3'd4;

  reg [2:0] st;
  reg [7:0] cnt;
  reg [3:0] edge_n;
  reg [7:0] osh, ish;

  assign tx_ready = (st == IDLE);

  wire       out_bit  = lsb_first ? osh[0] : osh[7];
  wire [7:0] osh_next = lsb_first ? {1'b0, osh[7:1]} : {osh[6:0], 1'b0};
  wire       leading  = !edge_n[0];             // edges 0,2,4.. move SCLK away from CPOL
  wire       sample   = leading ^ cpha;         // CPHA=0 samples on leading, CPHA=1 on trailing

  always @(posedge clk) begin
    rx_valid <= 1'b0;
    if (!rst_n) begin
      st   <= IDLE;
      sclk <= 1'b0;
      cs_n <= 1'b1;
      mosi <= 1'b0;
    end else case (st)
      IDLE: begin
        sclk <= cpol;
        if (tx_valid) begin
          cs_n   <= 1'b0;
          cnt    <= div - 1'b1;
          edge_n <= 4'd0;
          st     <= SETUP;
          if (!cpha) begin                       // CPHA=0: first bit is valid before the first edge
            mosi <= lsb_first ? tx_data[0] : tx_data[7];
            osh  <= lsb_first ? {1'b0, tx_data[7:1]} : {tx_data[6:0], 1'b0};
          end else
            osh  <= tx_data;
        end
      end
      SETUP: if (cnt != 0) cnt <= cnt - 1'b1; else st <= XFER;
      XFER:
        if (cnt != 0) cnt <= cnt - 1'b1;
        else begin
          cnt    <= div - 1'b1;
          sclk   <= !sclk;
          edge_n <= edge_n + 1'b1;
          if (sample)
            ish <= lsb_first ? {miso, ish[7:1]} : {ish[6:0], miso};
          else begin
            mosi <= out_bit;
            osh  <= osh_next;
          end
          if (edge_n == 4'd15) st <= HOLD;
        end
      HOLD: if (cnt != 0) cnt <= cnt - 1'b1;
            else begin
              cs_n     <= 1'b1;
              rx_valid <= 1'b1;
              rx_data  <= ish;
              cnt      <= div - 1'b1;
              st       <= GAP;
            end
      default: if (cnt != 0) cnt <= cnt - 1'b1; else st <= IDLE;   // GAP
    endcase
  end
endmodule
