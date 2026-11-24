# I2C controller VIP

DUT: `rtl/i2c_master.v`, a byte-command I2C controller: START / repeated START, WRITE byte (reports the target's
ACK/NACK), READ byte (ACK or NACK it), STOP. Open-drain pins, clock stretching, and arbitration-loss detection for
multi-master buses. One divider sets all bus timing; with `div = f_clk / 400 kHz` every Standard-mode limit of
the NXP I2C specification (UM10204) is met.

The testbench top `tb/i2c_tb_top.v` builds the wired-AND bus: the controller, the cocotb target and a second
"rogue" master all pull the same SCL and SDA lines.

## VIP (`tb/i2c_vip.py`)

* `I2cTarget`: reactive EEPROM-style target (7-bit address, register pointer, auto-increment), written from the bus
  rules, not from the RTL. It stretches the clock at random for a random time, NACKs data on request, and can
  delay its SDA changes after SCL falls (hold time).
* `I2cMonitor`: passive decoder for START, repeated START, STOP, bytes and ACK bits, from the two wires alone.
  It checks tLOW, tHIGH, SCL period, tSU;STA, tHD;STA, tSU;STO, tBUF and tSU;DAT, and that no START or STOP lands
  inside a byte. Limits are either the UM10204 Standard-mode table or the controller's own promise at the
  current divider.
* `Coverage`: 29 bins.

## Verification plan

| Feature | How it is checked | Test |
|---|---|---|
| Idle bus after reset, commands without the bus are dropped | lines stay high, monitor sees nothing | `reset_idle` |
| Standard-mode timing (100 kHz) | monitor against the UM10204 table, 300 ns target hold time | `standard_mode_timing` |
| Exact bus sequence for write, combined read, current-address read | monitor tokens compared with the expected sequence | `directed` |
| Address NACK and data NACK reported, write stops after a NACK | controller flags + target memory | `directed`, `constrained_random` |
| Clock stretching on every bit | target stretches every SCL low phase, data and timing still correct | `clock_stretching` |
| Arbitration loss at the first differing bit, lines released, controller usable afterwards | second master on the bus, bit count at loss compared with the expected bit | `arbitration` |
| Data integrity across random traffic | target memory compared with a scoreboard mirror, every read checked | `constrained_random` |

Coverage: transaction kind (write, combined read, current-address read) x burst length (1, 2-3, 4+), address
ACK/NACK, data NACK, stretching per kind, data classes for written and read bytes, three dividers.

## Bugs it catches

9 injected bugs, all caught (see [../docs/MUTATION_REPORT.md](../docs/MUTATION_REPORT.md)), among them: clock
stretching ignored, arbitration not detected, arbitration lost but not reported, START hold time or STOP setup time
one quarter short, and SDA levels before STOP and repeated START swapped.

```bash
make -C tb            # SEED=1234 make -C tb
```
