# UART VIP

DUT: `rtl/uart.v`, 8N1 / 8E1 / 8O1, runtime baud divisor, TX with valid/ready, RX with a 2-flop synchroniser,
mid-bit sampling, start-bit glitch rejection, parity and framing error flags, BREAK handling.

VIP (`tb/uart_vip.py`): `UartSource` drives the serial line with any baud skew and can inject parity errors,
framing errors, BREAKs and glitches. `UartSink` decodes the DUT's TX line independently of the DUT's receiver.

Tests: reset state, every parity mode in both directions, error injection (parity, framing, glitch, BREAK),
constrained random over divisors 16/25/54, parity modes, ±2% baud skew and injected errors until coverage closes.

Coverage (37 bins): direction x parity mode x data class (0x00, 0xFF, 0x55/0xAA, other), direction x divisor,
RX baud skew, every error kind detected.

```bash
make            # SEED=1234 make
```
