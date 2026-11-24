# Multi-clock formal proof of async_fifo with Yosys + yosys-smtbmc + Z3 (no commercial tools).
# clk2fflogic turns both clock inputs into ordinary signals sampled on one global formal clock,
# so the solver explores every ordering of wclk and rclk edges.
#   make bmc     bounded model check, DEPTH steps of the global clock (about 10 edges of each clock)
#   make prove   k-induction: properties hold for ALL reachable states (the invariants are inductive
#                in very few steps, so this is fast)
#   make cover   traces reaching the cover points (full, read of the tracked word, full after wrap)
DEPTH ?= 20
KIND  ?= 8
# a 2-bit word and depth 4 are enough to exercise every pointer relation
WIDTH ?= 2
AW    ?= 2
TOP   := async_fifo
RTL   ?= $(CURDIR)/../rtl/async_fifo.v
BUILD ?= build
SMT   := $(BUILD)/$(TOP).smt2

all: bmc prove cover

$(SMT): $(RTL)
	mkdir -p $(BUILD)
	yosys -q -p 'read_verilog -sv -formal $(RTL); chparam -set WIDTH $(WIDTH) -set AW $(AW) $(TOP); prep -top $(TOP); memory_map; opt -fast; clk2fflogic; opt_clean; write_smt2 -wires $@'

bmc: $(SMT)
	yosys-smtbmc -s z3 --unroll --presat -t $(DEPTH) --dump-vcd $(BUILD)/bmc.vcd $(SMT)

prove: $(SMT)
	yosys-smtbmc -s z3 --unroll -i -t $(KIND) --dump-vcd $(BUILD)/induct.vcd $(SMT)

cover: $(SMT)
	yosys-smtbmc -s z3 --unroll -c -t 30 --dump-vcd $(BUILD)/cover.vcd $(SMT)

clean:
	rm -rf $(BUILD)
.DELETE_ON_ERROR:
.PHONY: all bmc prove cover clean
