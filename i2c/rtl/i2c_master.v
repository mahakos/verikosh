// I2C controller (single master with arbitration detection), byte-command interface.
//
//  * Commands: START (also a repeated START while the bus is owned), WRITE byte, READ byte, STOP.
//    One command at a time: cmd_ready is high while idle or while holding the bus between bytes.
//    WRITE, READ and STOP sent while the bus is not owned are dropped (done pulses, no bus activity).
//  * Open-drain pins: scl_oe / sda_oe = 1 pull the line low, 0 release it. scl_i / sda_i are the
//    wired-AND bus levels and are synchronised internally.
//  * Timing in units of div clk cycles (one quarter of the SCL period):
//      tLOW = tHIGH = 2*div, tSU;DAT = div, tSU;STA = tHD;STA = tSU;STO = 2*div, tBUF >= 3*div.
//    With div = f_clk / 400 kHz every Standard-mode (100 kHz) limit of UM10204 is met.
//  * Clock stretching: the SCL high phase only starts counting once SCL is really high.
//  * Arbitration: if SDA reads low while this master releases it to send a 1, the master leaves
//    both lines released, goes idle and reports arb_lost.
//  * SCL is never pulled low while the bus is free; START is the first thing on the bus.
//  * Reset: both lines released, busy, done, nack, arb_lost low, rd_data 0, cmd_ready high.
module i2c_master (
  input  wire        clk,
  input  wire        rst_n,
  input  wire [15:0] div,
  // command interface
  input  wire        cmd_valid,
  output wire        cmd_ready,
  input  wire [1:0]  cmd,          // 0 START, 1 WRITE, 2 READ, 3 STOP
  input  wire [7:0]  cmd_data,     // WRITE: byte to send
  input  wire        cmd_ack,      // READ: 1 = ACK the byte, 0 = NACK it (last byte)
  output reg         done,         // one-cycle pulse when a command finishes
  output reg  [7:0]  rd_data,      // READ result, valid with done
  output reg         nack,         // WRITE: the target did not acknowledge, valid with done
  output reg         arb_lost,     // valid with done
  output wire        busy,         // this master owns the bus (between START and STOP)
  // bus
  input  wire        scl_i,
  input  wire        sda_i,
  output reg         scl_oe,
  output reg         sda_oe
);
  localparam [1:0] C_START = 2'd0, C_WRITE = 2'd1, C_READ = 2'd2, C_STOP = 2'd3;
  localparam [1:0] IDLE = 2'd0, OWN = 2'd1, RUN = 2'd2;

  reg [1:0]  st, op, q;
  reg [3:0]  nbit;
  reg [8:0]  tx, rx;
  reg [16:0] cnt;
  reg [1:0]  scl_q, sda_q;
  wire       scl_s = scl_q[1];
  wire       sda_s = sda_q[1];
  wire [16:0] one_q = {1'b0, div} - 17'd1;
  wire [16:0] two_q = {div, 1'b0} - 17'd1;
  wire       is_bit = (op == C_WRITE) || (op == C_READ);

  assign cmd_ready = (st != RUN);
  assign busy      = (st != IDLE);

  // Synchronisers have no reset: they keep sampling the bus during reset, so they hold the real
  // line levels when reset is released (reset must be held for at least 2 clocks).
  always @(posedge clk) begin
    scl_q <= {scl_q[0], scl_i};
    sda_q <= {sda_q[0], sda_i};
  end

  always @(posedge clk) begin
    done <= 1'b0;
    if (!rst_n) begin
      st       <= IDLE;
      scl_oe   <= 1'b0;
      sda_oe   <= 1'b0;
      nack     <= 1'b0;
      arb_lost <= 1'b0;
      rd_data  <= 8'h00;
    end else case (st)
      IDLE, OWN: if (cmd_valid) begin
        op       <= cmd;
        nbit     <= 4'd0;
        arb_lost <= 1'b0;
        tx       <= (cmd == C_WRITE) ? {cmd_data, 1'b1} : {8'hFF, !cmd_ack};
        if (st == IDLE && cmd != C_START)
          done <= 1'b1;                       // nothing to do without the bus
        else if (st == IDLE) begin            // START from a free bus: SCL is already high
          st  <= RUN;
          q   <= 2'd2;
          cnt <= two_q;
        end else begin
          st  <= RUN;
          q   <= 2'd0;
          cnt <= one_q;
        end
      end
      default: case (q)                       // RUN
        2'd0: if (cnt != 0) cnt <= cnt - 1'b1;  // SCL low (already pulled), SDA unchanged (hold)
              else begin
                q      <= 2'd1;
                cnt    <= one_q;
                sda_oe <= is_bit ? !tx[8] : (op == C_STOP);
              end
        2'd1: if (cnt != 0) cnt <= cnt - 1'b1;  // SCL low, SDA set up
              else begin
                q      <= 2'd2;
                scl_oe <= 1'b0;
                cnt    <= is_bit ? one_q : two_q;
              end
        2'd2: if (scl_s) begin                // SCL released; count only once it is high
                if (cnt != 0) cnt <= cnt - 1'b1;
                else begin
                  q   <= 2'd3;
                  cnt <= (op == C_START) ? two_q : one_q;
                  if (is_bit) begin
                    rx <= {rx[7:0], sda_s};
                    if (op == C_WRITE && nbit != 4'd8 && tx[8] && !sda_s) begin
                      // someone else drove a 0: arbitration lost. Both lines are already
                      // released here (SCL in its high phase, SDA released to send the 1).
                      st       <= IDLE;
                      arb_lost <= 1'b1;
                      done     <= 1'b1;
                    end
                  end else
                    sda_oe <= (op == C_START);  // START: SDA falls, STOP: SDA rises
                end
              end
        default: if (cnt != 0) cnt <= cnt - 1'b1;  // SCL high, SDA held
          else if (is_bit && nbit != 4'd8) begin
            q      <= 2'd0;
            cnt    <= one_q;
            nbit   <= nbit + 1'b1;
            tx     <= tx << 1;
            scl_oe <= 1'b1;
          end else begin
            done <= 1'b1;
            if (op == C_STOP)
              st <= IDLE;
            else begin
              st     <= OWN;
              scl_oe <= 1'b1;                 // hold SCL low until the next command
              if (op == C_READ)  rd_data <= rx[8:1];
              if (op == C_WRITE) nack    <= rx[0];
            end
          end
      endcase
    endcase
  end
endmodule
