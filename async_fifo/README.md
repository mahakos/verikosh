# Dual-clock FIFO (clock-domain crossing): simulation + multi-clock formal proof

DUT: `rtl/async_fifo.v`, the classic Gray-pointer asynchronous FIFO: binary pointers address the memory, Gray copies
cross the clock boundary through 2-flop synchronisers, and registered, pessimistic full and empty flags.
First-word fall-through, power-of-two depth, a synchronous reset per domain.

CDC bugs are the ones that pass every ordinary simulation and then fail in silicon or on the bench, so this block is
verified twice, in two independent ways.

## Simulation (`tb/`)

* `JitterClock`: both clocks get random cycle-to-cycle jitter and their periods change on the fly, so the two
  domains drift through every phase relation (5 ratio bands, from write clock much faster to read clock much faster)
* `Writer` / `Reader`: random bursty traffic; they also *try* to push while full and pop while empty
* scoreboard: every popped word is compared with a reference queue
* `FlagChecker`: `wfull` is never low while the FIFO really is full, `rempty` never low while it really is empty
  (no overflow, no underflow), and each flag releases within `SYNC_STAGES + 1` cycles of its own clock after the
  other side moved (no stuck or over-pessimistic flag)
* `GrayChecker`: looks inside the DUT; each crossing pointer must be the Gray code of its binary pointer and
  change by at most one bit per clock
* `Coverage`: 17 bins: clock-ratio band x {reached full, reached empty}, blocked push, blocked pop, occupancy
  levels, pointers wrapped twice

## Formal (`formal/`)

`clk2fflogic` turns both clocks into ordinary inputs sampled on one global formal clock, so the solver tries
**every ordering and phase of the two clocks' edges**, not just the ones a testbench happens to produce.
Properties are in the RTL under `` `ifdef FORMAL ``:

* never overflows or underflows; crossing pointers are always valid Gray code and change at most one bit at a time
* every synchroniser stage holds a pointer value the other side really had, older stages hold older values, and
  so `wfull` / `rempty` are exactly the full / empty test on a stale (therefore safe) pointer
* data integrity: one arbitrary word written at an arbitrary pointer is still in memory while live and is exactly
  what is read out
* covers: full, the tracked word read out, full again after the pointers wrapped

`make` runs a 20-step BMC, **k-induction (a full proof for all reachable states, both clocks unconstrained)**
and the cover traces, in about 20 seconds. The formal run uses a 2-bit word and depth 4; the simulation uses the
default 8-bit word and depth 8.

## Verification plan

| Feature | Simulation | Formal |
|---|---|---|
| Reset: empty, not full | `reset_flags` (also checked during reset) | reset-state invariants |
| Exactly DEPTH words accepted, extra pushes refused, order kept | `fill_and_drain` (fast and slow write clock) | overflow + data integrity |
| No overflow / underflow for any clock relation | `FlagChecker` in `cdc_random` | assertions, proven by induction |
| Flags release promptly | `FlagChecker` latency bound | n/a (liveness) |
| Gray encoding of crossing pointers | `GrayChecker` | assertions |
| Data integrity | scoreboard | `anyconst` tracked word |

## Bugs it catches

9 injected CDC and flag bugs, every one caught by simulation and by formal independently: pointer crossing in binary, full
test with only the MSB inverted (the binary rule applied to Gray code), full or empty flag one cycle late,
push while full, pop while empty, a synchroniser fed from combinational logic, memory read with the Gray pointer,
empty low out of reset.

What neither method here can show: metastability itself, and whether the synthesised netlist keeps the
synchroniser flops together. Those need a structural CDC check on the netlist and timing constraints
(`set_max_delay` / `set_false_path` on the crossing), which are part of a real sign-off.

```bash
make -C tb            # SEED=1234 make -C tb
make -C formal        # bmc + prove + cover
```
