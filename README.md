# Neesil VIP: open-source verification IP

[![VIP regression](https://github.com/mahakos/verikosh/actions/workflows/ci.yml/badge.svg)](https://github.com/mahakos/verikosh/actions/workflows/ci.yml)

Reusable, coverage-driven verification components for common SoC interfaces, built only on open-source tools:
[cocotb](https://www.cocotb.org) + Icarus Verilog for simulation, Yosys + yosys-smtbmc + Z3 for formal, Verilator for lint.

Every block ships with an RTL design, a reusable VIP, a self-checking regression with functional coverage,
and a list of realistic injected bugs that the regression has to catch.

| Block | Verification | Tests | Coverage | Injected bugs caught |
|---|---|---|---|---|
| [**APB3**](apb) register file | cocotb VIP, protocol checker, random wait states | 4 | 158 bins incl. register x strobe cross | 8 / 8 |
| [**AXI4-Lite**](axi4lite) register file | cocotb VIP with random back-pressure **+ formal proof** of the handshake rules | 4 + BMC + k-induction | 76 bins incl. channel order x back-pressure | 8 / 8 |
| [**Sync FIFO**](fifo) | **formal only**: full proof by k-induction, incl. data integrity | BMC + k-induction + 3 covers | n/a | 5 / 5 |
| [**UART**](uart) transceiver | cocotb VIP: parity, framing, BREAK, glitches, ±2% baud skew | 4 | 37 bins | 8 / 8 |
| [**SPI**](spi) master | cocotb slave model + protocol checker, all 4 modes, MSB/LSB first | 3 | 33 bins incl. mode x bit order x divider | 7 / 7 |
| [**I2C**](i2c) controller | cocotb target model + bus monitor checking UM10204 timing, clock stretching, **multi-master arbitration** | 6 | 29 bins incl. kind x burst length | 9 / 9 |
| [**Dual-clock FIFO**](async_fifo) (CDC) | jitter clocks, flag-safety and Gray-code checkers **+ multi-clock formal proof** over every clock-edge ordering | 3 + BMC + k-induction + 3 covers | 17 bins incl. clock ratio x full/empty | 9 / 9 |

**Mutation score: 54 / 54 (100%).** See [docs/MUTATION_REPORT.md](docs/MUTATION_REPORT.md).
How the flow works, step by step: [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

## Why mutation testing

Passing tests and 100% coverage only say the testbench *ran*. They do not say it would notice a bug.
[`tools/mutate.py`](tools/mutate.py) injects one realistic bug at a time into the RTL (wrong byte lane, off-by-one range
check, dropped VALID, wrong parity polarity, missing CS hold time ...), reruns the regression and records whether it failed.
A bug that survives is a hole in the verification, and gets fixed in the testbench.

Things this found while building the repo:

* Two AXI4-Lite bugs (`ARREADY` high while a read response is pending, `WREADY` ignoring a full buffer) slip past the
  simulation, because the master issues one transaction at a time. The **formal** proof catches both in under two seconds.
  This is the practical argument for running formal next to simulation.
* An SPI mutant that removed the CS-to-SCLK hold time survived the first version of the testbench. The protocol checker
  now measures setup and hold, and the mutant is caught.
* The dual-clock FIFO's classic CDC mistakes (pointer crossing in binary, full test with only the MSB inverted,
  a synchroniser fed from combinational logic) are each caught twice: by jittered-clock simulation and by a formal
  proof that tries every ordering of the two clocks' edges.
* Two mutants turned out to be *equivalent* (no observable difference for any legal stimulus) and were replaced, which is
  itself a useful review of the design.

## Repository layout

```
<block>/rtl/        design under test (Verilog-2005, lint-clean with verilator -Wall)
<block>/tb/         cocotb VIP + tests + Makefile
<block>/formal/     Yosys/SMT formal setup (fifo, axi4lite, async_fifo); properties live in the RTL under `ifdef FORMAL
<block>/mutants.txt injected-bug list for tools/mutate.py
tools/mutate.py     mutation-testing driver
docs/               methodology and generated reports
```

## Run it

```bash
pip install "cocotb>=2.0,<3"
sudo apt install iverilog yosys z3 verilator     # Ubuntu 24.04 packages

make -C apb/tb              # any of apb, axi4lite, uart, spi, i2c, async_fifo.  SEED=1234 make ... reproduces a run
make -C fifo/formal         # bmc + prove + cover   (also axi4lite/formal, async_fifo/formal)
python3 tools/mutate.py     # mutation score for every block, about 4 minutes
```

CI runs lint, every simulation with three seeds (coverage goes to the job summary), all three formal proofs and the
full mutation run on every push.

## Need verification help?

Neesil builds UVM and cocotb testbenches, writes formal properties, closes coverage and triages bugs for small chip and FPGA teams.
Free 1-week verification health check: **hello.neesil@gmail.com** · https://mahakos.github.io/mahakosh.github.io/

License: Apache-2.0
