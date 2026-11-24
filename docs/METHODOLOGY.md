# How Neesil verifies a block

This repository is a working sample of the flow Neesil uses on client designs. Every block here went through the
same steps, and each step leaves something you can rerun.

## 1. Verification plan first

Before writing a testbench we list every feature of the block, how it will be checked (scoreboard, protocol
checker, assertion, formal property) and which coverage bin proves it was exercised. The plan is a table in the
block's README (see [i2c](../i2c/README.md) and [async_fifo](../async_fifo/README.md)). If a feature has no check and
no bin, it is not verified, whatever the pass rate says.

## 2. Independent models

The VIP is written from the protocol or the spec, never from the RTL. The I2C target, the SPI slave and the UART
sink each decode the wires on their own. A model copied from the RTL agrees with the RTL's bugs.

## 3. Checkers that run all the time

Protocol checkers (AXI VALID/READY rules, I2C bus timing, SPI chip-select setup and hold, CDC flag safety) run
in every test, so a violation is caught in whatever test produces it, not only in the test written for it.

## 4. Constrained-random to coverage closure

Random tests run until functional coverage reaches 100%, with crosses where features interact (mode x bit order x
divider, clock ratio x full/empty). CI runs every block with fixed seeds and a random seed, and the seed is
printed so any failure can be replayed exactly.

## 5. Formal where simulation is weak

Handshake rules, FIFOs and clock-domain crossings get formal properties proven by k-induction with Yosys and
SMT solvers. A proof covers every reachable state; for the dual-clock FIFO it also covers every ordering of the
two clocks' edges. In this repo, two AXI4-Lite bugs pass simulation and are caught only by the proof.

## 6. Mutation testing: does the testbench actually catch bugs?

Coverage says the stimulus happened. It does not say a check would fail. [`tools/mutate.py`](../tools/mutate.py)
injects one realistic bug at a time and reruns the regression; a bug that survives is a hole in the
verification and gets fixed in the testbench. Current score: see [MUTATION_REPORT.md](MUTATION_REPORT.md).

## 7. Honest limits

Each block README says what the verification here does not cover (for example, metastability and netlist-level
CDC structure for the dual-clock FIFO). A sign-off report that claims everything is a report nobody should trust.

## What a client gets

For a client block the same flow produces: the verification plan, the testbench and VIP in the client's
language and simulator (SystemVerilog/UVM or cocotb), the formal setup where it pays off, a coverage report, a
mutation report, a list of bugs found with reproducing seeds, and a CI job that reruns all of it. Everything is
handed over; nothing depends on Neesil staying involved.
