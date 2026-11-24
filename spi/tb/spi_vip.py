"""Neesil SPI VIP (cocotb).

  SpiSlave        reactive slave model for any CPOL/CPHA/bit order: returns queued MISO bytes,
                  records MOSI bytes. Written from the SPI timing rules, not from the RTL.
  SpiChecker      passive: SCLK idles at CPOL, exactly 16 edges per CS window, minimum
                  SCLK half-period, CS_N setup and hold times
  Coverage        cross mode x bit order x divider, data classes on both lines, back-to-back frames
"""
from collections import Counter, deque
from dataclasses import dataclass
import cocotb
from cocotb.triggers import Edge, FallingEdge, RisingEdge, First
from cocotb.utils import get_sim_time


@dataclass
class SpiConfig:
    cpol: int = 0
    cpha: int = 0
    lsb_first: int = 0
    div: int = 2

    @property
    def mode(self):
        return self.cpol * 2 + self.cpha


def data_class(b):
    return {0x00: "zero", 0xFF: "ones", 0x55: "alt", 0xAA: "alt"}.get(b, "other")


class SpiSlave:
    def __init__(self, dut, cfg):
        self.dut, self.cfg = dut, cfg
        self.replies = deque()
        self.received = []
        dut.miso.value = 0

    def _bits(self, byte):
        order = range(8) if self.cfg.lsb_first else range(7, -1, -1)
        return [(byte >> i) & 1 for i in order]

    async def run(self):
        d = self.dut
        while True:
            await FallingEdge(d.cs_n)
            reply = self.replies.popleft() if self.replies else 0
            out = self._bits(reply)
            got = []
            if not self.cfg.cpha:
                d.miso.value = out.pop(0)
            while len(got) < 8:
                ev = await First(Edge(d.sclk), RisingEdge(d.cs_n))
                if d.cs_n.value:
                    break
                leading = int(d.sclk.value) != self.cfg.cpol
                if leading ^ bool(self.cfg.cpha):          # sampling edge
                    got.append(int(d.mosi.value))
                elif out:                                   # driving edge
                    d.miso.value = out.pop(0)
            if self.cfg.lsb_first:
                got = got[::-1]
            byte = 0
            for b in got:
                byte = (byte << 1) | b
            self.received.append(byte if len(got) == 8 else None)


class SpiChecker:
    """Cycle-exact timing of the master, from the RTL header: CS_N setup to the first SCLK edge is
    div+1 clocks, every SCLK half-period is div clocks, CS_N hold after the last edge is div clocks,
    CS_N stays high at least div+1 clocks between frames (exactly div+1 back to back), each CS window
    has exactly 16 SCLK edges, and SCLK sits at CPOL while CS_N is high."""

    def __init__(self, dut, cfg, clk_ps, log):
        self.dut, self.cfg, self.clk_ps, self.log = dut, cfg, clk_ps, log
        self.violations = 0
        self.frames = 0

    def _fail(self, msg):
        self.violations += 1
        self.log.error(f"SPI protocol violation: {msg}")

    def _exact(self, what, got_ps, clocks):
        if got_ps != clocks * self.clk_ps:
            self._fail(f"{what} = {got_ps / self.clk_ps:g} clocks, expected {clocks}")

    async def run(self):
        d = self.dut
        cocotb.start_soon(self._idle_level())
        t_rise = None
        while True:
            await FallingEdge(d.cs_n)
            start = get_sim_time("ps")
            div = self.cfg.div
            if t_rise is not None:
                gap = start - t_rise
                if gap < (div + 1) * self.clk_ps:
                    self._fail(f"CS_N high for {gap / self.clk_ps:g} clocks between frames, minimum {div + 1}")
                elif back_to_back and d.tx_valid.value:
                    self._exact("CS_N high time between back-to-back frames", gap, div + 1)
            edges, last = 0, start
            while True:
                await First(Edge(d.sclk), RisingEdge(d.cs_n))
                now = get_sim_time("ps")
                if d.cs_n.value:
                    self._exact("CS_N hold after the last SCLK edge", now - last, div)
                    break
                if edges == 0:
                    self._exact("CS_N setup to the first SCLK edge", now - last, div + 1)
                else:
                    self._exact("SCLK half-period", now - last, div)
                last = now
                edges += 1
            t_rise = get_sim_time("ps")
            back_to_back = bool(d.tx_valid.value)
            self.frames += 1
            if edges != 16:
                self._fail(f"{edges} SCLK edges in one CS window, expected 16")

    async def _idle_level(self):
        d = self.dut
        while True:
            await Edge(d.sclk)
            if d.cs_n.value and int(d.sclk.value) != self.cfg.cpol:
                self._fail("SCLK left its idle level while CS_N is high")


class Coverage:
    DIVS = (1, 2, 5)

    def __init__(self):
        self.hits = Counter()

    def sample(self, cfg, mosi, miso, back_to_back):
        self.hits[f"mode{cfg.mode}:{'lsb' if cfg.lsb_first else 'msb'}:div{cfg.div}"] += 1
        self.hits[f"mosi:{data_class(mosi)}"] += 1
        self.hits[f"miso:{data_class(miso)}"] += 1
        if back_to_back:
            self.hits["back_to_back"] += 1

    def bins(self):
        b = [f"mode{m}:{o}:div{v}" for m in range(4) for o in ("msb", "lsb") for v in self.DIVS]
        b += [f"{l}:{c}" for l in ("mosi", "miso") for c in ("zero", "ones", "alt", "other")]
        b += ["back_to_back"]
        return b

    def report(self):
        bins = self.bins()
        miss = [x for x in bins if not self.hits[x]]
        return 100.0 * (len(bins) - len(miss)) / len(bins), miss, len(bins)
