# SPI master VIP

DUT: `rtl/spi_master.v`, all four modes (CPOL/CPHA), MSB- or LSB-first, 8-bit frames, runtime SCLK divider,
back-to-back frames.

VIP (`tb/spi_vip.py`): `SpiSlave` is a reactive slave model written from the SPI timing rules (not from the RTL):
it answers with queued MISO bytes and records MOSI. `SpiChecker` checks that SCLK idles at CPOL, every CS window has
exactly 16 edges, and the SCLK half-period, CS setup and CS hold are at least the configured divider.

Tests: reset idle level (follows CPOL), one exchange in each of the 8 mode x bit-order combinations, constrained random
including bursts, until coverage closes.

Coverage (33 bins): mode x bit order x divider cross (24), data classes on MOSI and MISO, back-to-back frames.

```bash
make            # SEED=1234 make
```
