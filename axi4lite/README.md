# AXI4-Lite VIP + formal

DUT: `rtl/axil_regfile.v`, 15 RW registers + a read-only VERSION register, independent AW and W channels with
one-entry buffers (either may arrive first), SLVERR for out-of-range addresses and writes to the read-only register.

## Simulation (`tb/`)

* `AxilMaster` drives AW and W independently with random delays, so all three orderings (AW first, W first, same cycle) occur
* `AxilReadyDriver` randomly back-pressures BREADY and RREADY
* `AxilMonitor` rebuilds transactions from the five channel handshakes
* `ProtocolChecker` checks the AXI rule on every channel: VALID must stay high and payload stable until READY
* `RegModel` + `Scoreboard`, and `Coverage` with crosses (76 bins)

Tests: reset values, directed access incl. byte strobes and both error cases, channel ordering, constrained random
with back-pressure until coverage closes.

## Formal (`formal/`)

Properties are in the RTL under `` `ifdef FORMAL ``. The master's obligations are assumed and the slave's are asserted:

* after reset BVALID and RVALID are low
* BVALID/RVALID stay high and BRESP/RDATA/RRESP stay stable until accepted
* no write response without both an accepted AW and W, no read response without an AR; at most 2 writes and 1 read in flight
* reads of the VERSION register always return VERSION with OKAY

`make` runs a 10-step BMC, **k-induction (a full proof for all reachable states)** and 5 cover traces
(OKAY and SLVERR writes, SLVERR read, AW running ahead under B back-pressure, W arriving before AW).

Two of the injected bugs are caught only by the formal proof; see the top-level README.

```bash
make -C tb
make -C formal
```
