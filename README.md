# Neesil VIP: open-source verification IP

Reusable, coverage-driven verification components for common SoC interfaces, built on open-source tools ([cocotb](https://www.cocotb.org), Icarus Verilog / Verilator).

| VIP | Status | Contents |
|---|---|---|
| **APB3** | ✅ v0.1 | Master driver, passive monitor, register reference model, scoreboard, functional coverage, directed + constrained-random tests |
| SPI | planned | |
| I2C | planned | |
| UART | planned | |
| AXI4-Lite | planned | + SymbiYosys formal properties |

## APB3 VIP

```
apb/
  rtl/apb_regfile.v        # example DUT: 8x32-bit APB3 register file with PSTRB and PSLVERR
  tb/apb_vip.py            # VIP: ApbMaster, ApbMonitor, RegModel, Scoreboard, Coverage
  tb/test_apb_regfile.py   # tests: directed write/read, error responses, constrained random
  tb/Makefile
```

**Coverage model (36 bins):** read and write to every register, unaligned and out-of-range accesses for both directions, and all 16 byte-strobe patterns. The random test runs until coverage reaches 100%.

**Bug-finding check:** swapping one byte-strobe bit in the RTL makes the constrained-random test fail with a READ mismatch, so the scoreboard catches real bugs.

### Run it

```bash
pip install cocotb          # cocotb 2.x
sudo apt install iverilog   # or: brew install icarus-verilog
cd apb/tb
make                        # SEED=1234 make  to reproduce a run
```

Expected: `TESTS=3 PASS=3 FAIL=0`.

## Need verification help?

Neesil builds UVM and cocotb testbenches, closes coverage and triages bugs for small chip and FPGA teams.
Free 1-week verification health check: **hello.neesil@gmail.com** · https://mahakos.github.io/mahakosh.github.io/

License: Apache-2.0
