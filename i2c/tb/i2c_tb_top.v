// Test harness: wired-AND open-drain bus between the controller and the cocotb target model.
// The cocotb target drives tgt_scl_oe / tgt_sda_oe; a second (rogue) master in the arbitration
// test drives rogue_sda_oe.
`timescale 1ns/1ps
module i2c_tb_top (
  input  wire        clk,
  input  wire        rst_n,
  input  wire [15:0] div,
  input  wire        cmd_valid,
  output wire        cmd_ready,
  input  wire [1:0]  cmd,
  input  wire [7:0]  cmd_data,
  input  wire        cmd_ack,
  output wire        done,
  output wire [7:0]  rd_data,
  output wire        nack,
  output wire        arb_lost,
  output wire        busy,
  input  wire        tgt_scl_oe,
  input  wire        tgt_sda_oe,
  input  wire        rogue_sda_oe,
  output wire        scl,
  output wire        sda,
  output wire        m_scl_oe,
  output wire        m_sda_oe
);
  assign scl = !(m_scl_oe || tgt_scl_oe);
  assign sda = !(m_sda_oe || tgt_sda_oe || rogue_sda_oe);

  i2c_master dut (
    .clk(clk), .rst_n(rst_n), .div(div),
    .cmd_valid(cmd_valid), .cmd_ready(cmd_ready), .cmd(cmd), .cmd_data(cmd_data), .cmd_ack(cmd_ack),
    .done(done), .rd_data(rd_data), .nack(nack), .arb_lost(arb_lost), .busy(busy),
    .scl_i(scl), .sda_i(sda), .scl_oe(m_scl_oe), .sda_oe(m_sda_oe)
  );
endmodule
