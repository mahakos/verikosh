SIM ?= icarus
TOPLEVEL_LANG = verilog
RTL ?= $(CURDIR)/../rtl/spi_master.v
VERILOG_SOURCES = $(RTL)
COCOTB_TOPLEVEL = spi_master
COCOTB_TEST_MODULES = test_spi_master
COCOTB_HDL_TIMEPRECISION = 1ps
include $(shell cocotb-config --makefiles)/Makefile.sim
