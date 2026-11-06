# Synchronous FIFO: formal proof

DUT: `rtl/sync_fifo.v`, first-word fall-through, power-of-two depth, simultaneous push and pop.

Verified with formal only (Yosys + yosys-smtbmc + Z3), no simulation:

* invariants: `count <= DEPTH`, `empty` and `full` agree with `count`, never both
* count moves by exactly +1, -1 or 0 per cycle, no overflow and no underflow
* **data integrity**: an arbitrary value written to an arbitrary slot (`anyconst`) stays in memory while it is live
  and is exactly what `rdata` shows when it reaches the head
* covers: full, concurrent push and pop, drained after the pointers wrapped

`make` runs a 20-step BMC and k-induction, which proves the properties for every reachable state, not just 20 cycles.
All 5 injected bugs (inverted full flag, overflow, underflow, wrong read pointer, memory clobbered on a refused push)
are caught.

```bash
make -C formal
```
