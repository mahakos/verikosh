# Formal proof of sync_fifo with Yosys + yosys-smtbmc + Z3 (no commercial tools).
#   make bmc     bounded model check, DEPTH steps from reset
#   make prove   k-induction: properties hold for ALL reachable states
#   make cover   generate traces that reach the cover points
#   make         all three
DEPTH ?= 20
TOP   := sync_fifo
RTL   ?= $(CURDIR)/../rtl/sync_fifo.v
BUILD ?= build
SMT   := $(BUILD)/$(TOP).smt2

all: bmc prove cover

$(SMT): $(RTL)
	mkdir -p $(BUILD)
	yosys -q -p 'read_verilog -formal $(RTL); prep -top $(TOP); memory_map; opt -fast; dffunmap; write_smt2 -wires $@'

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
