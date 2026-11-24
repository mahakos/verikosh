"""Neesil UART VIP (cocotb).

  UartConfig   divisor, parity mode, clock period
  UartSource   drives a serial line: any baud skew, parity/framing error and glitch injection
  UartSink     decodes a serial line (independent of the DUT's receiver)
  Coverage     data classes x parity mode x direction, error kinds, divisors, skew
"""
from collections import Counter
from dataclasses import dataclass
from cocotb.triggers import Timer, RisingEdge, FallingEdge, ReadOnly

PARITY = ("none", "even", "odd")


@dataclass
class UartConfig:
    div: int = 16
    parity: str = "none"
    clk_ps: int = 10_000

    @property
    def bit_ps(self):
        return self.div * self.clk_ps

    def parity_bit(self, byte):
        p = bin(byte).count("1") & 1
        return p if self.parity == "even" else p ^ 1


@dataclass
class UartFrame:
    data: int
    perr: bool = False
    ferr: bool = False


def data_class(b):
    return {0x00: "zero", 0xFF: "ones", 0x55: "alt", 0xAA: "alt"}.get(b, "other")


class UartSource:
    def __init__(self, line, cfg):
        self.line, self.cfg = line, cfg
        line.value = 1

    async def send(self, byte, skew=0.0, bad_parity=False, bad_stop=False):
        bit = round(self.cfg.bit_ps * (1.0 + skew))
        bits = [0] + [(byte >> i) & 1 for i in range(8)]
        if self.cfg.parity != "none":
            bits.append(self.cfg.parity_bit(byte) ^ int(bad_parity))
        bits.append(0 if bad_stop else 1)
        for b in bits:
            self.line.value = b
            await Timer(bit, unit="ps")
        self.line.value = 1
        if bad_stop:                       # give the receiver an idle bit to recover
            await Timer(bit, unit="ps")

    async def send_windowed(self, byte, lo_ps, hi_ps):
        """Each data and parity bit shows its true value only between centre+lo_ps and centre+hi_ps and
        the opposite value for the rest of the bit. A receiver that does not sample inside that window
        reads garbage. Start and stop bits are normal."""
        bit = self.cfg.bit_ps
        bits = [(byte >> i) & 1 for i in range(8)]
        if self.cfg.parity != "none":
            bits.append(self.cfg.parity_bit(byte))
        self.line.value = 0
        await Timer(bit, unit="ps")
        a, b = bit // 2 + lo_ps, bit // 2 + hi_ps
        for v in bits:
            self.line.value = v ^ 1
            await Timer(a, unit="ps")
            self.line.value = v
            await Timer(b - a, unit="ps")
            self.line.value = v ^ 1
            await Timer(bit - b, unit="ps")
        self.line.value = 1
        await Timer(bit * 2, unit="ps")

    async def line_break(self, bits=24):
        """Hold the line low for many bit times (a BREAK), then release it."""
        self.line.value = 0
        await Timer(self.cfg.bit_ps * bits, unit="ps")
        self.line.value = 1
        await Timer(self.cfg.bit_ps * 2, unit="ps")

    async def glitch(self, fraction=0.25):
        self.line.value = 0
        await Timer(round(self.cfg.bit_ps * fraction), unit="ps")
        self.line.value = 1
        await Timer(self.cfg.bit_ps * 2, unit="ps")


class UartSink:
    """Decodes frames on a line, sampling at mid-bit, and hands them to callbacks."""

    def __init__(self, line, cfg, callbacks):
        self.line, self.cfg, self.cbs = line, cfg, callbacks

    async def run(self):
        while True:
            await FallingEdge(self.line)
            await Timer(self.cfg.bit_ps // 2, unit="ps")
            if self.line.value:
                continue
            byte = 0
            for i in range(8):
                await Timer(self.cfg.bit_ps, unit="ps")
                byte |= int(self.line.value) << i
            perr = False
            if self.cfg.parity != "none":
                await Timer(self.cfg.bit_ps, unit="ps")
                perr = int(self.line.value) != self.cfg.parity_bit(byte)
            await Timer(self.cfg.bit_ps, unit="ps")
            f = UartFrame(byte, perr, not int(self.line.value))
            for cb in self.cbs:
                cb(f)


class TxTiming:
    """Cycle-exact check of the transmitter: every edge of txd inside a frame falls on a bit boundary
    (a multiple of div clocks after the start edge), the frame lasts exactly 10 or 11 bit times, and
    tx_ready rises exactly when the stop bit ends."""

    def __init__(self, dut, cfg, log):
        self.dut, self.cfg, self.log = dut, cfg, log
        self.violations = 0
        self.frames = 0

    def _fail(self, msg):
        self.violations += 1
        self.log.error(f"TX timing: {msg}")

    async def run(self):
        d = self.dut
        prev_txd, prev_ready, n = 1, 1, None
        while True:
            await RisingEdge(d.clk)
            await ReadOnly()
            if not d.rst_n.value:
                prev_txd, prev_ready, n = 1, 1, None
                continue
            txd, ready = int(d.txd.value), int(d.tx_ready.value)
            if n is None:
                if prev_txd and not txd:                    # start bit begins
                    n, div = 0, self.cfg.div
                    nbits = 11 if self.cfg.parity != "none" else 10
            else:
                n += 1
                if txd != prev_txd and n % div:
                    self._fail(f"txd changed {n} clocks into the frame, not on a bit boundary (div={div})")
                if ready and not prev_ready:
                    if n != nbits * div:
                        self._fail(f"tx_ready rose after {n} clocks, frame is {nbits * div}")
                    n = None
                    self.frames += 1
                elif n > nbits * div:
                    self._fail(f"frame longer than {nbits} bits")
                    n = None
            prev_txd, prev_ready = txd, ready


class RxMonitor:
    """Collects frames reported by the DUT receiver (rx_valid pulses)."""

    def __init__(self, dut, clk):
        self.dut, self.clk = dut, clk
        self.frames = []

    async def run(self):
        d = self.dut
        while True:
            await RisingEdge(self.clk)
            await ReadOnly()
            if d.rx_valid.value:
                self.frames.append(UartFrame(int(d.rx_data.value), bool(d.rx_perr.value), bool(d.rx_ferr.value)))


class Coverage:
    DIVS = (16, 25, 54)
    SKEWS = ("slow", "nominal", "fast")

    def __init__(self):
        self.hits = Counter()

    def sample(self, direction, cfg, frame, skew="nominal", err=None):
        self.hits[f"{direction}:{cfg.parity}:{data_class(frame)}"] += 1
        self.hits[f"{direction}:div{cfg.div}"] += 1
        if direction == "rx":
            self.hits[f"rx:skew:{skew}"] += 1
        if err:
            self.hits[f"err:{err}"] += 1

    def bins(self):
        b = [f"{d}:{p}:{c}" for d in ("tx", "rx") for p in PARITY for c in ("zero", "ones", "alt", "other")]
        b += [f"{d}:div{v}" for d in ("tx", "rx") for v in self.DIVS]
        b += [f"rx:skew:{s}" for s in self.SKEWS]
        b += ["err:parity_even", "err:parity_odd", "err:framing", "err:glitch"]
        return b

    def report(self):
        bins = self.bins()
        miss = [x for x in bins if not self.hits[x]]
        return 100.0 * (len(bins) - len(miss)) / len(bins), miss, len(bins)
