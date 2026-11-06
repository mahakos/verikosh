"""Neesil AXI4-Lite VIP (cocotb).

Components
  AxilMaster      drives AW/W/AR with independent, randomised VALID delays and channel order
  AxilReadyDriver randomly back-pressures BREADY and RREADY
  AxilMonitor     passive: rebuilds write/read transactions from channel handshakes
  ProtocolChecker passive: VALID must stay high and payload stable until READY (AXI A3.2.1)
  RegModel, Scoreboard, Coverage
"""
import random
from collections import Counter, deque
from dataclasses import dataclass
import cocotb
from cocotb.triggers import RisingEdge, ReadOnly

OKAY, SLVERR = 0, 2


def _int(sig, name="signal"):
    """Read a signal as int; X/Z becomes a clean test failure instead of a ValueError."""
    v = sig.value
    if not v.is_resolvable:
        raise AssertionError(f"{name} is X/Z ({v}) where a known value is required")
    return int(v)


@dataclass
class AxilTxn:
    write: bool
    addr: int
    data: int = 0
    strb: int = 0xF
    resp: int = OKAY
    order: str = ""          # write channel order seen by the slave: aw_first / w_first / same
    bp: bool = False         # response was back-pressured at least one cycle
    start: int = 0
    end: int = 0


class AxilMaster:
    """Issues one transaction at a time but drives the AW and W channels independently."""

    TIMEOUT = 200     # cycles; a hung slave fails the test instead of hanging the simulation

    def __init__(self, dut, clk, max_delay=3):
        self.dut, self.clk, self.max_delay = dut, clk, max_delay
        for s in ("awvalid", "wvalid", "arvalid", "awaddr", "wdata", "wstrb", "araddr", "awprot", "arprot"):
            getattr(dut, s).value = 0

    async def _channel(self, valid, ready, payload, delay):
        # drive on a clock edge, hold until handshake, then drop VALID
        for _ in range(delay):
            await RisingEdge(self.clk)
        for sig, val in payload:
            sig.value = val
        valid.value = 1
        for _ in range(self.TIMEOUT):
            await ReadOnly()
            handshake = bool(ready.value)
            await RisingEdge(self.clk)
            if handshake:
                break
        else:
            raise AssertionError(f"{valid._name}: no READY within {self.TIMEOUT} cycles")
        valid.value = 0

    async def write(self, addr, data, strb=0xF, aw_delay=None, w_delay=None):
        d = self.dut
        aw_delay = random.randint(0, self.max_delay) if aw_delay is None else aw_delay
        w_delay = random.randint(0, self.max_delay) if w_delay is None else w_delay
        await RisingEdge(self.clk)
        aw = cocotb.start_soon(self._channel(d.awvalid, d.awready, [(d.awaddr, addr)], aw_delay))
        w = cocotb.start_soon(self._channel(d.wvalid, d.wready, [(d.wdata, data), (d.wstrb, strb)], w_delay))
        await aw
        await w
        for _ in range(self.TIMEOUT):
            await ReadOnly()
            if d.bvalid.value and d.bready.value:
                resp = _int(d.bresp, name="BRESP")
                await RisingEdge(self.clk)
                return resp
            await RisingEdge(self.clk)
        raise AssertionError(f"no write response within {self.TIMEOUT} cycles")

    async def read(self, addr, ar_delay=None):
        d = self.dut
        ar_delay = random.randint(0, self.max_delay) if ar_delay is None else ar_delay
        await RisingEdge(self.clk)
        await self._channel(d.arvalid, d.arready, [(d.araddr, addr)], ar_delay)
        for _ in range(self.TIMEOUT):
            await ReadOnly()
            if d.rvalid.value and d.rready.value:
                data, resp = _int(d.rdata, name="RDATA"), _int(d.rresp, name="RRESP")
                await RisingEdge(self.clk)
                return data, resp
            await RisingEdge(self.clk)
        raise AssertionError(f"no read response within {self.TIMEOUT} cycles")


class AxilReadyDriver:
    """Randomly toggles BREADY/RREADY. p_ready=1.0 means always ready."""

    def __init__(self, dut, clk, p_ready=0.6):
        self.dut, self.clk, self.p = dut, clk, p_ready
        dut.bready.value = 1
        dut.rready.value = 1

    async def run(self):
        while True:
            await RisingEdge(self.clk)
            self.dut.bready.value = int(random.random() < self.p)
            self.dut.rready.value = int(random.random() < self.p)


class AxilMonitor:
    """Reconstructs transactions from handshakes on all five channels."""

    def __init__(self, dut, clk, callbacks):
        self.dut, self.clk, self.cbs = dut, clk, callbacks
        self.cycle = 0
        self.aw, self.w, self.ar = deque(), deque(), deque()
        self.b_wait = self.r_wait = 0

    async def run(self):
        d = self.dut
        while True:
            await RisingEdge(self.clk)
            await ReadOnly()
            self.cycle += 1
            if not d.aresetn.value:
                self.aw.clear(); self.w.clear(); self.ar.clear()
                continue
            aw_hs = d.awvalid.value and d.awready.value
            w_hs = d.wvalid.value and d.wready.value
            if aw_hs:
                self.aw.append((_int(d.awaddr, name="AWADDR"), self.cycle))
            if w_hs:
                self.w.append((_int(d.wdata, name="WDATA"), _int(d.wstrb, name="WSTRB"), self.cycle))
            if d.arvalid.value and d.arready.value:
                self.ar.append((_int(d.araddr, name="ARADDR"), self.cycle))
            if d.bvalid.value:
                if d.bready.value:
                    if not self.aw or not self.w:
                        raise AssertionError("BVALID handshake without a matching AW and W")
                    (addr, ca), (data, strb, cw) = self.aw.popleft(), self.w.popleft()
                    order = "same" if ca == cw else ("aw_first" if ca < cw else "w_first")
                    t = AxilTxn(True, addr, data, strb, _int(d.bresp, name="BRESP"), order,
                                self.b_wait > 0, min(ca, cw), self.cycle)
                    self.b_wait = 0
                    self._emit(t)
                else:
                    self.b_wait += 1
            if d.rvalid.value:
                if d.rready.value:
                    if not self.ar:
                        raise AssertionError("RVALID handshake without a matching AR")
                    addr, ca = self.ar.popleft()
                    t = AxilTxn(False, addr, _int(d.rdata, name="RDATA"), 0,
                                _int(d.rresp, name="RRESP"), "", self.r_wait > 0, ca, self.cycle)
                    self.r_wait = 0
                    self._emit(t)
                else:
                    self.r_wait += 1

    def _emit(self, t):
        for cb in self.cbs:
            cb(t)


class ProtocolChecker:
    """Checks the AXI handshake rule on every channel, for both master and slave sides."""

    CHANNELS = {
        "AW": ("awvalid", "awready", ("awaddr", "awprot")),
        "W": ("wvalid", "wready", ("wdata", "wstrb")),
        "B": ("bvalid", "bready", ("bresp",)),
        "AR": ("arvalid", "arready", ("araddr", "arprot")),
        "R": ("rvalid", "rready", ("rdata", "rresp")),
    }

    def __init__(self, dut, clk, log):
        self.dut, self.clk, self.log = dut, clk, log
        self.violations = 0

    async def run(self):
        d = self.dut
        prev = {}
        while True:
            await RisingEdge(self.clk)
            await ReadOnly()
            if not d.aresetn.value:
                prev = {}
                continue
            for ch, (v, r, pl) in self.CHANNELS.items():
                valid, ready = bool(getattr(d, v).value), bool(getattr(d, r).value)
                cur = tuple(str(getattr(d, p).value) for p in pl)
                if ch in prev:
                    pvalid, pready, ppl = prev[ch]
                    if pvalid and not pready:
                        if not valid:
                            self._fail(f"{ch}: VALID dropped before READY")
                        elif cur != ppl:
                            self._fail(f"{ch}: payload changed while VALID && !READY")
                prev[ch] = (valid, ready, cur)

    def _fail(self, msg):
        self.violations += 1
        self.log.error(f"AXI protocol violation: {msg}")


class RegModel:
    def __init__(self, nregs=16, version=0x4E450001):
        self.n, self.version = nregs, version
        self.mem = [0] * (nregs - 1)

    def predict(self, t):
        idx = (t.addr >> 2) & 0x3F
        if t.write:
            if idx >= self.n - 1:
                return SLVERR, None
            for b in range(4):
                if t.strb >> b & 1:
                    m = 0xFF << (8 * b)
                    self.mem[idx] = (self.mem[idx] & ~m) | (t.data & m)
            return OKAY, None
        if idx < self.n - 1:
            return OKAY, self.mem[idx]
        if idx == self.n - 1:
            return OKAY, self.version
        return SLVERR, 0


class Scoreboard:
    def __init__(self, model, log):
        self.model, self.log = model, log
        self.checked = self.errors = 0

    def __call__(self, t):
        exp_resp, exp_data = self.model.predict(t)
        self.checked += 1
        kind = "WR" if t.write else "RD"
        if t.resp != exp_resp:
            self.errors += 1
            self.log.error(f"{kind} 0x{t.addr:02x}: resp {t.resp} expected {exp_resp}")
        if exp_data is not None and t.data != exp_data:
            self.errors += 1
            self.log.error(f"RD 0x{t.addr:02x}: data 0x{t.data:08x} expected 0x{exp_data:08x}")


class Coverage:
    """Functional coverage with crosses.

    point  dir x region       {wr,rd} x {rw_reg, ro_reg, out_of_range}
    point  reg                every RW register written and read
    cross  order x bp         write channel order {aw_first, w_first, same} x back-pressured B {yes,no}
    cross  strb x region      all 16 WSTRB values x {rw_reg, illegal}
    point  read bp            back-pressured R {yes,no}
    """

    def __init__(self, nregs=16):
        self.n = nregs
        self.hits = Counter()

    def _region(self, addr):
        idx = addr >> 2
        return "rw" if idx < self.n - 1 else ("ro" if idx == self.n - 1 else "oor")

    def __call__(self, t):
        reg = self._region(t.addr)
        k = "wr" if t.write else "rd"
        self.hits[f"{k}:{reg}"] += 1
        if reg == "rw":
            self.hits[f"{k}:reg{t.addr >> 2}"] += 1
        if t.write:
            self.hits[f"order:{t.order}:bp{int(t.bp)}"] += 1
            self.hits[f"strb:{t.strb:04b}:{'rw' if reg == 'rw' else 'illegal'}"] += 1
        else:
            self.hits[f"rbp:{int(t.bp)}"] += 1

    def bins(self):
        b = [f"{k}:{r}" for k in ("wr", "rd") for r in ("rw", "ro", "oor")]
        b += [f"{k}:reg{i}" for k in ("wr", "rd") for i in range(self.n - 1)]
        b += [f"order:{o}:bp{p}" for o in ("aw_first", "w_first", "same") for p in (0, 1)]
        b += [f"strb:{s:04b}:{r}" for s in range(16) for r in ("rw", "illegal")]
        b += ["rbp:0", "rbp:1"]
        return b

    def report(self):
        bins = self.bins()
        miss = [x for x in bins if not self.hits[x]]
        return 100.0 * (len(bins) - len(miss)) / len(bins), miss, len(bins)
