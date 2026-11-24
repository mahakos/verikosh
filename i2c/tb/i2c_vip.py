"""Neesil I2C VIP (cocotb).

  I2cTiming     minimum bus timings; UM10204 Standard-mode limits or limits scaled to the divider
  I2cTarget     reactive EEPROM-style target (7-bit address, register pointer, auto-increment),
                written from the I2C bus rules, not from the RTL. Injects clock stretching,
                data NACKs and a configurable SDA hold time.
  I2cMonitor    passive: decodes START / repeated START / STOP / bytes + ACK from the wires alone,
                and checks tLOW, tHIGH, tSU;STA, tHD;STA, tSU;STO, tBUF, tSU;DAT and START/STOP
                placement (never inside a byte)
  Coverage      transaction kinds, burst lengths, address match, data NACK, stretching,
                data classes, divider
"""
import random
from collections import Counter
from dataclasses import dataclass
import cocotb
from cocotb.triggers import First, Timer
from cocotb.utils import get_sim_time


@dataclass
class I2cTiming:
    low: int        # all values in ps
    high: int
    su_sta: int
    hd_sta: int
    su_sto: int
    buf: int
    su_dat: int
    period: int

    @classmethod
    def standard_mode(cls):
        """UM10204 rev. 7, table 10, Standard-mode (100 kHz)."""
        return cls(low=4_700_000, high=4_000_000, su_sta=4_700_000, hd_sta=4_000_000,
                   su_sto=4_000_000, buf=4_700_000, su_dat=250_000, period=10_000_000)

    @classmethod
    def scaled(cls, div, clk_ps):
        """What the controller promises for a given divider: tLOW = tHIGH = 2 quarters, etc."""
        q = div * clk_ps
        return cls(low=2 * q, high=2 * q, su_sta=2 * q, hd_sta=2 * q, su_sto=2 * q,
                   buf=3 * q, su_dat=q, period=4 * q)


def data_class(b):
    return {0x00: "zero", 0xFF: "ones", 0x55: "alt", 0xAA: "alt"}.get(b, "other")


class I2cTarget:
    """Memory target: write [addr+W][ptr][data...], read [addr+R][data...] from ptr, auto-increment."""

    def __init__(self, dut, addr=0x50, hold_ps=0):
        self.dut, self.addr, self.hold_ps = dut, addr, hold_ps
        self.mem = [0] * 256
        self.ptr = 0
        self.stretch_prob = 0.0
        self.stretch_ps = (0, 0)
        self.nack_after = None     # NACK the data byte with this index in a write (0 = first)
        self.stretched = 0         # SCL low phases this target extended
        dut.tgt_scl_oe.value = 0
        dut.tgt_sda_oe.value = 0

    def _drive(self, bit):
        """Release (1) or pull (0) SDA, after the hold time."""
        if self.hold_ps:
            cocotb.start_soon(self._late(bit))
        else:
            self.dut.tgt_sda_oe.value = int(not bit)

    async def _late(self, bit):
        await Timer(self.hold_ps, unit="ps")
        self.dut.tgt_sda_oe.value = int(not bit)

    async def _stretch(self, t):
        self.dut.tgt_scl_oe.value = 1
        await Timer(t, unit="ps")
        self.dut.tgt_scl_oe.value = 0

    def _ack_slot(self, byte):
        """Called on the SCL fall after the 8th bit of a byte the target received. True = ACK."""
        if self.state == "addr":
            if byte >> 1 != self.addr:
                self.state = "ignore"
                return False
            self.state = "read_ack" if byte & 1 else "ptr"
            return True
        if self.state == "ptr":
            self.ptr, self.state, self.nwritten = byte, "wdata", 0
            return True
        if self.state == "wdata":
            if self.nack_after is not None and self.nwritten >= self.nack_after:
                self.state = "ignore"
                return False
            self.mem[self.ptr] = byte
            self.ptr = (self.ptr + 1) & 0xFF
            self.nwritten += 1
            return True
        return False

    def _load(self):
        self.tx = self.mem[self.ptr]
        self.ptr = (self.ptr + 1) & 0xFF

    async def run(self):
        d = self.dut
        self.state, bits, sh, mack = "idle", 0, 0, False
        scl_p, sda_p = 1, 1
        while True:
            await First(d.scl.value_change, d.sda.value_change)
            scl, sda = int(d.scl.value), int(d.sda.value)
            if scl and scl_p and sda != sda_p:                 # START or STOP
                self._drive(1)
                self.state = "addr" if sda == 0 else "idle"
                bits, sh = 0, 0
            elif scl and not scl_p:                            # SCL rising: sample
                bits += 1
                if bits <= 8:
                    sh = ((sh << 1) | sda) & 0xFF
                elif self.state == "read":
                    mack = sda == 0
            elif scl_p and not scl:                            # SCL falling: drive
                if self.state != "idle" and random.random() < self.stretch_prob:
                    self.stretched += 1
                    cocotb.start_soon(self._stretch(random.randint(*self.stretch_ps)))
                if bits == 8:
                    if self.state == "read":
                        self._drive(1)                         # master drives the ACK bit
                    else:
                        self._drive(0 if self._ack_slot(sh) else 1)
                elif bits == 9:
                    bits, sh = 0, 0
                    if self.state == "read_ack" or (self.state == "read" and mack):
                        self.state = "read"
                        self._load()
                        self._drive(self.tx >> 7 & 1)
                    else:
                        if self.state == "read":
                            self.state = "ignore"
                        self._drive(1)
                elif self.state == "read" and 1 <= bits <= 7:
                    self._drive(self.tx >> (7 - bits) & 1)
            scl_p, sda_p = scl, sda


class I2cMonitor:
    """Decodes the bus into tokens: ("S",), ("Sr",), ("P",), ("B", byte, acked)."""

    def __init__(self, dut, timing, log):
        self.dut, self.t, self.log = dut, timing, log
        self.tokens = []
        self.violations = 0
        self.allow_abort = False     # arbitration test: the winning master may stop anywhere

    def _fail(self, msg):
        self.violations += 1
        self.log.error(f"I2C bus violation: {msg}")

    def _min(self, name, got, limit):
        if got < limit:
            self._fail(f"{name} = {got / 1e3:.1f} ns < {limit / 1e3:.1f} ns")

    async def run(self):
        d, t = self.dut, self.t
        scl_p, sda_p = 1, 1
        busy, bits, sh = False, 0, 0
        t_rise = t_fall = t_sda = t_start = t_stop = None
        after_start = False
        while True:
            await First(d.scl.value_change, d.sda.value_change)
            now = get_sim_time("ps")
            scl, sda = int(d.scl.value), int(d.sda.value)
            if scl and scl_p and sda != sda_p:
                # the SCL rise just before a STOP or repeated START is not a data bit, hence bits > 1
                if bits > 1 and not self.allow_abort:
                    self._fail(f"{'START' if sda == 0 else 'STOP'} after {bits} bits of a byte")
                if sda == 0:
                    if busy:
                        self._min("tSU;STA", now - t_rise, t.su_sta)
                        self.tokens.append(("Sr",))
                    else:
                        if t_stop is not None:
                            self._min("tBUF", now - t_stop, t.buf)
                        self.tokens.append(("S",))
                    busy, after_start, t_start = True, True, now
                else:
                    if t_rise is not None and not self.allow_abort:
                        self._min("tSU;STO", now - t_rise, t.su_sto)
                    self.tokens.append(("P",))
                    busy, t_stop = False, now
                bits, sh = 0, 0
            elif scl and not scl_p:
                if busy and not after_start:
                    self._min("tLOW", now - t_fall, t.low)
                if busy and t_sda is not None and t_sda > t_fall:
                    self._min("tSU;DAT", now - t_sda, t.su_dat)
                if busy and t_rise is not None and not after_start:
                    self._min("SCL period", now - t_rise, t.period)
                t_rise = now
                bits += 1
                if bits <= 8:
                    sh = (sh << 1) | sda
                else:
                    self.tokens.append(("B", sh, sda == 0))
                    bits, sh = 0, 0
            elif scl_p and not scl:
                if after_start:
                    self._min("tHD;STA", now - t_start, t.hd_sta)
                    after_start = False
                elif t_rise is not None:
                    self._min("tHIGH", now - t_rise, t.high)
                t_fall = now
            if sda != sda_p and not (scl and scl_p):
                t_sda = now
            scl_p, sda_p = scl, sda


class Coverage:
    DIVS = (3, 5, 8)
    LENS = ("1", "2-3", "4+")

    def __init__(self):
        self.hits = Counter()

    @staticmethod
    def _len(n):
        return "1" if n == 1 else "2-3" if n <= 3 else "4+"

    def sample(self, kind, n=0, data=(), div=None, stretched=False, addr_hit=True, data_nack=False):
        h = self.hits
        h[f"kind:{kind}"] += 1
        h[f"div:{div}"] += 1
        if not addr_hit:
            h["addr:nack"] += 1
            return
        h["addr:ack"] += 1
        if kind in ("write", "read", "write_read") and n:
            h[f"{kind}:len{self._len(n)}"] += 1
        for b in data:
            h[f"{'wdata' if kind == 'write' else 'rdata'}:{data_class(b)}"] += 1
        if data_nack:
            h["write:data_nack"] += 1
        if stretched:
            h[f"stretch:{kind}"] += 1

    def bins(self):
        b = [f"kind:{k}" for k in ("write", "read", "write_read")]
        b += [f"{k}:len{l}" for k in ("write", "read", "write_read") for l in self.LENS]
        b += ["addr:ack", "addr:nack", "write:data_nack"]
        b += [f"stretch:{k}" for k in ("write", "read", "write_read")]
        b += [f"{w}:{c}" for w in ("wdata", "rdata") for c in ("zero", "ones", "alt", "other")]
        b += [f"div:{v}" for v in self.DIVS]
        return b

    def report(self):
        bins = self.bins()
        miss = [x for x in bins if not self.hits[x]]
        return 100.0 * (len(bins) - len(miss)) / len(bins), miss, len(bins)
