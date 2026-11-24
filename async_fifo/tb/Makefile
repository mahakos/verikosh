SIM ?= icarus
TOPLEVEL_LANG = verilog
RTL ?= $(CURDIR)/../rtl/async_fifo.v
VERILOG_SOURCES = $(RTL)
COCOTB_TOPLEVEL = async_fifo
COCOTB_TEST_MODULES = test_async_fifo
COCOTB_HDL_TIMEPRECISION = 1ps
include $(shell cocotb-config --makefiles)/Makefile.sim
