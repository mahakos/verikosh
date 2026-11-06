SIM ?= icarus
TOPLEVEL_LANG = verilog
RTL ?= $(CURDIR)/../rtl/uart.v
VERILOG_SOURCES = $(RTL)
COCOTB_TOPLEVEL = uart
COCOTB_TEST_MODULES = test_uart
COCOTB_HDL_TIMEPRECISION = 1ps
include $(shell cocotb-config --makefiles)/Makefile.sim
