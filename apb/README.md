# APB3 VIP

DUT: `rtl/apb_regfile.v`, 8 x 32-bit registers, `PSTRB`, `PSLVERR` on unaligned or out-of-range addresses,
0 to 3 pseudo-random wait states per transfer (`WAIT_MAX` parameter).

VIP (`tb/apb_vip.py`): `ApbMaster` (with a PREADY hang timeout), passive `ApbMonitor`, `ApbProtocolChecker`
(setup lasts one cycle, PENABLE only with PSEL, address/control stable through wait states), `RegModel`,
`Scoreboard`, `Coverage`.

Tests: reset values, directed write/read, error responses leave state untouched, constrained random until
coverage closes (typically 1,100 to 1,800 transfers).

Coverage (158 bins): direction x target (every register, unaligned, out of range), every register crossed with every
PSTRB pattern, direction x 0..3 wait states, error responses that were delayed by wait states.

X/Z on PRDATA, PSLVERR or the address/data buses fails the test with a clear message instead of a Python exception.

```bash
make            # SEED=1234 make
```
