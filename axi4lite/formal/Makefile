# Formal proof of axil_regfile with Yosys + yosys-smtbmc + Z3 (no commercial tools).
#   make bmc     bounded model check, DEPTH steps from reset
#   make prove   k-induction: properties hold for ALL reachable states
#   make cover   generate traces that reach the cover points
#   make         all three
DEPTH ?= 10
# A smaller instance keeps the proof fast; the properties do not depend on the size.
PARAMS ?= chparam -set NREGS 4 -set AW 5 $(TOP);
TOP   := axil_regfile
RTL   ?= $(CURDIR)/../rtl/axil_regfile.v
BUILD ?= build
SMT   := $(BUILD)/$(TOP).smt2

all: bmc prove cover

$(SMT): $(RTL)
	mkdir -p $(BUILD)
	yosys -q -p 'read_verilog -formal $(RTL); $(PARAMS) prep -top $(TOP); memory_map; opt -fast; dffunmap; write_smt2 -wires $@'

bmc: $(SMT)
	yosys-smtbmc -s z3 --unroll --presat -t $(DEPTH) --dump-vcd $(BUILD)/bmc.vcd $(SMT)

prove: $(SMT)
	yosys-smtbmc -s z3 --unroll -i -t $(DEPTH) --dump-vcd $(BUILD)/induct.vcd $(SMT)

cover: $(SMT)
	yosys-smtbmc -s z3 --unroll -c -t $(DEPTH) --dump-vcd $(BUILD)/cover.vcd $(SMT)

clean:
	rm -rf $(BUILD)
.DELETE_ON_ERROR:
.PHONY: all bmc prove cover clean
