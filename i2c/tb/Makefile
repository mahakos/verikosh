SIM ?= icarus
TOPLEVEL_LANG = verilog
RTL ?= $(CURDIR)/../rtl/i2c_master.v
VERILOG_SOURCES = $(RTL) $(CURDIR)/i2c_tb_top.v
COCOTB_TOPLEVEL = i2c_tb_top
COCOTB_TEST_MODULES = test_i2c_master
COCOTB_HDL_TIMEPRECISION = 1ps
include $(shell cocotb-config --makefiles)/Makefile.sim
