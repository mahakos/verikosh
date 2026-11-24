"""Neesil dual-clock FIFO VIP (cocotb).

  JitterClock   clock whose period can change at run time, with random cycle-to-cycle jitter, so the
                two domains drift through every phase relationship
  Writer        write-domain agent: random bursty pushes, also *attempts* pushes while full
  Reader        read-domain agent: random bursty pops, also attempts pops while empty, checks data
  FlagChecker   wfull is never low while the FIFO is really full, rempty never low while really empty
                (overflow / underflow safety), and both flags release within SYNC_STAGES + 1 cycles
                of their own clock after the other side moved (no stuck or over-pessimistic flag)
  GrayChecker   each Gray pointer changes by at most one bit per clock and matches its binary pointer
  Coverage      clock ratio x {reached full, reached empty}, blocked push, blocked pop, occupancy
                levels, pointer wrap-around
"""
import random
from collections import Counter, deque
import cocotb
from cocotb.triggers import RisingEdge, ReadOnly, Timer


class JitterClock:
    def __init__(self, sig, period_ps, jitter=0.1, rng=None):
        self.sig, self.period, self.jitter = sig, period_ps, jitter
        self.rng = rng or random.Random()

    async def run(self):
        self.sig.value = 0
        while True:
            half = self.period / 2 * (1 + self.rng.uniform(-self.jitter, self.jitter))
            await Timer(max(1, round(half)), unit="ps")
            self.sig.value = 1
            half = self.period / 2 * (1 + self.rng.uniform(-self.jitter, self.jitter))
            await Timer(max(1, round(half)), unit="ps")
            self.sig.value = 0


class Shared:
    """True state of the FIFO as the testbench sees it: what was pushed and popped, at the real edges."""

    def __init__(self, depth):
        self.depth = depth
        self.model = deque()
        self.pushes = 0
        self.pops = 0

    @property
    def occupancy(self):
        return self.pushes - self.pops


class Writer:
    def __init__(self, dut, sh, rng):
        self.dut, self.sh, self.rng = dut, sh, rng
        self.p_write = 0.5
        self.enabled = True
        self.blocked = 0
        dut.winc.value = 0
        dut.wdata.value = 0

    async def run(self, on_cycle):
        d, sh = self.dut, self.sh
        pending = False
        while True:
            await RisingEdge(d.wclk)
            if pending:
                sh.pushes += 1
            want = self.enabled and self.rng.random() < self.p_write
            data = self.rng.getrandbits(len(d.wdata))
            d.winc.value = int(want)
            d.wdata.value = data
            await ReadOnly()
            full = bool(d.wfull.value)
            pending = want and not full
            if pending:
                sh.model.append(data)
            elif want:
                self.blocked += 1
            on_cycle(full)


class Reader:
    def __init__(self, dut, sh, rng, log):
        self.dut, self.sh, self.rng, self.log = dut, sh, rng, log
        self.p_read = 0.5
        self.enabled = True
        self.blocked = 0
        self.errors = 0
        self.checked = 0
        dut.rinc.value = 0

    async def run(self, on_cycle):
        d, sh = self.dut, self.sh
        pending = False
        while True:
            await RisingEdge(d.rclk)
            if pending:
                sh.pops += 1
            want = self.enabled and self.rng.random() < self.p_read
            d.rinc.value = int(want)
            await ReadOnly()
            empty = bool(d.rempty.value)
            pending = want and not empty
            if pending:
                got = int(d.rdata.value)
                if not sh.model:
                    self.errors += 1
                    self.log.error(f"pop of 0x{got:02x} while the reference model is empty (underflow)")
                else:
                    exp = sh.model.popleft()
                    self.checked += 1
                    if got != exp:
                        self.errors += 1
                        self.log.error(f"data mismatch: got 0x{got:02x}, expected 0x{exp:02x}")
            elif want:
                self.blocked += 1
            on_cycle(empty)


class FlagChecker:
    def __init__(self, sh, sync_stages, log):
        self.sh, self.log = sh, log
        self.limit = sync_stages + 1      # sync stages + the flag register
        self.violations = 0
        self.stale_full = self.stale_empty = 0
        self.max_stale_full = self.max_stale_empty = 0

    def _fail(self, msg):
        self.violations += 1
        self.log.error(f"flag violation: {msg}")

    def write_side(self, full):
        occ = self.sh.occupancy
        if occ > self.sh.depth:
            self._fail(f"occupancy {occ} > depth {self.sh.depth}: overflow")
        if not full and occ >= self.sh.depth:
            self._fail("wfull low while the FIFO is full")
        self.stale_full = self.stale_full + 1 if (full and occ < self.sh.depth) else 0
        self.max_stale_full = max(self.max_stale_full, self.stale_full)
        if self.stale_full > self.limit:
            self._fail(f"wfull still high {self.stale_full} wclk cycles after space was freed")
            self.stale_full = 0

    def read_side(self, empty):
        occ = self.sh.occupancy
        if occ < 0:
            self._fail(f"occupancy {occ} < 0: underflow")
        if not empty and occ <= 0:
            self._fail("rempty low while the FIFO is empty")
        self.stale_empty = self.stale_empty + 1 if (empty and occ > 0) else 0
        self.max_stale_empty = max(self.max_stale_empty, self.stale_empty)
        if self.stale_empty > self.limit:
            self._fail(f"rempty still high {self.stale_empty} rclk cycles after data arrived")
            self.stale_empty = 0


def gray(b):
    return b ^ (b >> 1)


class GrayChecker:
    """Looks inside the DUT: the pointers that cross domains must be proper Gray code."""

    def __init__(self, dut, log):
        self.dut, self.log = dut, log
        self.violations = 0

    async def _watch(self, clk, gsig, bsig, name):
        prev = None
        while True:
            await RisingEdge(clk)
            await ReadOnly()
            g, b = int(gsig.value), int(bsig.value)
            if g != gray(b):
                self.violations += 1
                self.log.error(f"{name}: Gray pointer 0x{g:x} is not gray(0x{b:x})")
            if prev is not None and bin(g ^ prev).count("1") > 1:
                self.violations += 1
                self.log.error(f"{name}: Gray pointer changed 0x{prev:x} -> 0x{g:x} (more than one bit)")
            prev = g

    def start(self):
        d = self.dut
        cocotb.start_soon(self._watch(d.wclk, d.wgray, d.wbin, "write"))
        cocotb.start_soon(self._watch(d.rclk, d.rgray, d.rbin, "read"))


RATIOS = ("w_much_faster", "w_faster", "similar", "r_faster", "r_much_faster")


def ratio_bin(wper, rper):
    r = rper / wper                      # > 1: write clock is faster
    if r >= 2.0:
        return "w_much_faster"
    if r >= 1.2:
        return "w_faster"
    if r > 1 / 1.2:
        return "similar"
    if r > 0.5:
        return "r_faster"
    return "r_much_faster"


class Coverage:
    def __init__(self):
        self.hits = Counter()

    def bins(self):
        b = [f"{r}:{e}" for r in RATIOS for e in ("full", "empty")]
        b += ["blocked_push", "blocked_pop", "wrap2"]
        b += [f"occ:{o}" for o in ("zero", "low", "high", "full")]
        return b

    def sample_occ(self, occ, depth):
        if occ <= 0:
            self.hits["occ:zero"] += 1
        elif occ >= depth:
            self.hits["occ:full"] += 1
        elif occ <= depth // 2:
            self.hits["occ:low"] += 1
        else:
            self.hits["occ:high"] += 1

    def report(self):
        bins = self.bins()
        miss = [x for x in bins if not self.hits[x]]
        return 100.0 * (len(bins) - len(miss)) / len(bins), miss, len(bins)
