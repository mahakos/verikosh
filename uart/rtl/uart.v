// UART transceiver: 8 data bits, optional even/odd parity, 1 stop bit, runtime baud divisor.
//  * div      clocks per bit (>= 8). Change only while both directions are idle.
//  * TX       valid/ready byte interface, LSB first on txd.
//  * RX       2-flop synchroniser, mid-bit sampling, start-bit glitch rejection,
//             parity and framing error flags. After a framing error the receiver waits
//             for the line to return high before looking for the next start bit.
module uart #(
  parameter integer DW = 16
)(
  input  wire          clk,
  input  wire          rst_n,
  input  wire [DW-1:0] div,
  input  wire          par_en,
  input  wire          par_odd,
  // transmit
  input  wire [7:0]    tx_data,
  input  wire          tx_valid,
  output wire          tx_ready,
  output reg           txd,
  // receive
  input  wire          rxd,
  output reg  [7:0]    rx_data,
  output reg           rx_valid,
  output reg           rx_perr,
  output reg           rx_ferr
);
  localparam [2:0] IDLE = 3'd0, START = 3'd1, DATA = 3'd2, PAR = 3'd3, STOP = 3'd4, BREAK = 3'd5;

  // ------------------------------------------------------------------ transmitter
  reg [2:0]    tst;
  reg [DW-1:0] tcnt;
  reg [2:0]    tbit;
  reg [7:0]    tsh;
  reg          tpar;

  assign tx_ready = (tst == IDLE);

  always @(posedge clk) begin
    if (!rst_n) begin
      tst <= IDLE;
      txd <= 1'b1;
    end else if (tst == IDLE) begin
      txd <= 1'b1;
      if (tx_valid) begin
        tsh  <= tx_data;
        tpar <= ^tx_data ^ par_odd;
        tcnt <= div - 1'b1;
        txd  <= 1'b0;                       // start bit
        tst  <= START;
      end
    end else if (tcnt != 0) begin
      tcnt <= tcnt - 1'b1;
    end else begin
      tcnt <= div - 1'b1;
      case (tst)
        START: begin tst <= DATA; tbit <= 3'd0; txd <= tsh[0]; tsh <= tsh >> 1; end
        DATA:  if (tbit == 3'd7) begin
                 if (par_en) begin tst <= PAR;  txd <= tpar; end
                 else        begin tst <= STOP; txd <= 1'b1; end
               end else begin
                 tbit <= tbit + 1'b1; txd <= tsh[0]; tsh <= tsh >> 1;
               end
        PAR:   begin tst <= STOP; txd <= 1'b1; end
        default: tst <= IDLE;               // end of stop bit
      endcase
    end
  end

  // ------------------------------------------------------------------ receiver
  reg          rx_m, rx_s;                  // synchroniser
  reg [2:0]    rst_q;
  reg [DW-1:0] rcnt;
  reg [2:0]    rbit;
  reg [7:0]    rsh;
  reg          rpar;

  always @(posedge clk) begin
    if (!rst_n) begin
      rx_m <= 1'b1;
      rx_s <= 1'b1;
    end else begin
      rx_m <= rxd;
      rx_s <= rx_m;
    end
  end

  always @(posedge clk) begin
    rx_valid <= 1'b0;
    if (!rst_n) begin
      rst_q   <= IDLE;
      rx_data <= 8'h00;
      rx_perr <= 1'b0;
      rx_ferr <= 1'b0;
    end else case (rst_q)
      IDLE:  if (!rx_s) begin rcnt <= (div >> 1) - 1'b1; rst_q <= START; end
      BREAK: if (rx_s)  rst_q <= IDLE;
      default:
        if (rcnt != 0) rcnt <= rcnt - 1'b1;
        else begin
          rcnt <= div - 1'b1;
          case (rst_q)
            START: if (rx_s) rst_q <= IDLE;  // glitch, not a start bit
                   else begin rst_q <= DATA; rbit <= 3'd0; end
            DATA:  begin
                     rsh <= {rx_s, rsh[7:1]};
                     rbit <= rbit + 1'b1;
                     if (rbit == 3'd7) rst_q <= par_en ? PAR : STOP;
                   end
            PAR:   begin rpar <= rx_s; rst_q <= STOP; end
            default: begin                   // STOP
                     rx_valid <= 1'b1;
                     rx_data  <= rsh;
                     rx_ferr  <= !rx_s;
                     rx_perr  <= par_en && (rpar != (^rsh ^ par_odd));
                     rst_q    <= rx_s ? IDLE : BREAK;
                   end
          endcase
        end
    endcase
  end
endmodule
