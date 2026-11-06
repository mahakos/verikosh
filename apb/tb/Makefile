SIM ?= icarus
TOPLEVEL_LANG = verilog
RTL ?= $(CURDIR)/../rtl/apb_regfile.v
VERILOG_SOURCES = $(RTL)
COCOTB_TOPLEVEL = apb_regfile
COCOTB_TEST_MODULES = test_apb_regfile
include $(shell cocotb-config --makefiles)/Makefile.sim
