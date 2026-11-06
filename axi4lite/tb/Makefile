SIM ?= icarus
TOPLEVEL_LANG = verilog
RTL ?= $(CURDIR)/../rtl/axil_regfile.v
VERILOG_SOURCES = $(RTL)
COCOTB_TOPLEVEL = axil_regfile
COCOTB_TEST_MODULES = test_axil_regfile
include $(shell cocotb-config --makefiles)/Makefile.sim
