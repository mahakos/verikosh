"""Neesil APB3 VIP (cocotb): master driver, passive monitor, protocol checker, reference model,
scoreboard and functional coverage with crosses."""
import random
from dataclasses import dataclass
from collections import Counter
from cocotb.triggers import RisingEdge, ReadOnly


def _int(sig, name):
    """Read a signal as int. X/Z becomes a clear test failure instead of a Python ValueError."""
    v = sig.value
    if not v.is_resolvable:
        raise AssertionError(f"{name} is X/Z ({v}) at the end of a transfer")
    return int(v)


@dataclass
class ApbTxn:
    write: bool
    addr: int
    data: int = 0
    strb: int = 0xF
    slverr: bool = False
    waits: int = 0


class ApbMaster:
    """Drives APB3 setup + access phases and waits for PREADY (with a hang timeout)."""

    MAX_WAIT = 16

    def __init__(self, dut, clk):
        self.dut, self.clk = dut, clk
        dut.psel.value = 0
        dut.penable.value = 0
        dut.pwrite.value = 0
        dut.paddr.value = 0
        dut.pwdata.value = 0
        dut.pstrb.value = 0

    async def xfer(self, t: ApbTxn) -> ApbTxn:
        d = self.dut
        await RisingEdge(self.clk)
        d.psel.value, d.penable.value = 1, 0
        d.pwrite.value, d.paddr.value = int(t.write), t.addr
        d.pwdata.value = t.data if t.write else 0
        d.pstrb.value = t.strb if t.write else 0
        await RisingEdge(self.clk)
        d.penable.value = 1
        for _ in range(self.MAX_WAIT + 1):
            await ReadOnly()
            if d.pready.value:
                break
            await RisingEdge(self.clk)
        else:
            raise AssertionError(f"PREADY timeout: no response within {self.MAX_WAIT} wait states")
        if not t.write:
            t.data = _int(d.prdata, "PRDATA")
        t.slverr = bool(_int(d.pslverr, "PSLVERR"))
        await RisingEdge(self.clk)
        d.psel.value, d.penable.value = 0, 0
        return t


class ApbMonitor:
    """Passive monitor: samples every completed access and calls subscribers."""

    def __init__(self, dut, clk, callbacks):
        self.dut, self.clk, self.cbs = dut, clk, callbacks

    async def run(self):
        d = self.dut
        waits = 0
        while True:
            await RisingEdge(self.clk)
            await ReadOnly()
            if d.psel.value and d.penable.value:
                if not d.pready.value:
                    waits += 1
                    continue
                write = bool(d.pwrite.value)
                t = ApbTxn(
                    write=write,
                    addr=_int(d.paddr, "PADDR"),
                    data=_int(d.pwdata, "PWDATA") if write else _int(d.prdata, "PRDATA"),
                    strb=_int(d.pstrb, "PSTRB"),
                    slverr=bool(_int(d.pslverr, "PSLVERR")),
                    waits=waits,
                )
                waits = 0
                for cb in self.cbs:
                    cb(t)


class ApbProtocolChecker:
    """APB3 handshake rules (AMBA APB spec, section 3):
    setup phase lasts one cycle, PENABLE only with PSEL, address/control/write data stable
    from setup until PREADY, and PSEL/PENABLE behave correctly after the transfer."""

    def __init__(self, dut, clk, log):
        self.dut, self.clk, self.log = dut, clk, log
        self.violations = 0

    def _fail(self, msg):
        self.violations += 1
        self.log.error(f"APB protocol violation: {msg}")

    async def run(self):
        d = self.dut
        prev = None
        while True:
            await RisingEdge(self.clk)
            await ReadOnly()
            if not d.presetn.value:
                prev = None
                continue
            cur = dict(psel=int(d.psel.value), penable=int(d.penable.value), pready=int(d.pready.value),
                       ctl=(str(d.paddr.value), str(d.pwrite.value), str(d.pwdata.value), str(d.pstrb.value)))
            if cur["penable"] and not cur["psel"]:
                self._fail("PENABLE high without PSEL")
            if prev:
                if prev["psel"] and not prev["penable"]:            # previous cycle was SETUP
                    if not (cur["psel"] and cur["penable"]):
                        self._fail("SETUP phase not followed by ACCESS phase")
                    elif cur["ctl"] != prev["ctl"]:
                        self._fail("address/control changed between SETUP and ACCESS")
                if prev["psel"] and prev["penable"] and not prev["pready"]:   # wait state
                    if not (cur["psel"] and cur["penable"]):
                        self._fail("transfer abandoned during a wait state")
                    elif cur["ctl"] != prev["ctl"]:
                        self._fail("address/control changed during a wait state")
                if prev["psel"] and prev["penable"] and prev["pready"] and cur["penable"]:
                    self._fail("PENABLE still high in the cycle after the transfer completed")
            prev = cur


class RegModel:
    """Reference model of the register file."""

    def __init__(self, nregs=8):
        self.n = nregs
        self.mem = [0] * nregs

    def valid(self, addr):
        return addr % 4 == 0 and addr // 4 < self.n

    def predict(self, t: ApbTxn):
        """Return (expected_slverr, expected_rdata or None) and update state."""
        if not self.valid(t.addr):
            return True, None
        i = t.addr // 4
        if t.write:
            for b in range(4):
                if t.strb >> b & 1:
                    mask = 0xFF << (8 * b)
                    self.mem[i] = (self.mem[i] & ~mask) | (t.data & mask)
            return False, None
        return False, self.mem[i]


class Scoreboard:
    def __init__(self, model, log):
        self.model, self.log = model, log
        self.checked, self.errors = 0, 0

    def __call__(self, t: ApbTxn):
        exp_err, exp_data = self.model.predict(t)
        self.checked += 1
        if t.slverr != exp_err:
            self.errors += 1
            self.log.error(f"PSLVERR mismatch addr=0x{t.addr:02x} got={t.slverr} exp={exp_err}")
        if exp_data is not None and t.data != exp_data:
            self.errors += 1
            self.log.error(f"READ mismatch addr=0x{t.addr:02x} got=0x{t.data:08x} exp=0x{exp_data:08x}")


class Coverage:
    """Functional coverage.

    point  dir x target      {wr,rd} x {reg0..reg7, unaligned, out_of_range}    20 bins
    cross  reg x strb        every register written with every PSTRB pattern     128 bins
    cross  dir x waits       {wr,rd} x {0,1,2,3} wait states                    8 bins
    cross  dir x slverr x waits>0   error responses delayed by wait states      2 bins
    """

    def __init__(self, nregs=8):
        self.n = nregs
        self.hits = Counter()

    def __call__(self, t: ApbTxn):
        kind = "wr" if t.write else "rd"
        if t.addr % 4 == 0 and t.addr // 4 < self.n:
            target = f"reg{t.addr // 4}"
            if t.write:
                self.hits[f"{target}:strb_{t.strb:04b}"] += 1
        elif t.addr % 4:
            target = "unaligned"
        else:
            target = "out_of_range"
        self.hits[f"{kind}_{target}"] += 1
        self.hits[f"{kind}_wait{min(t.waits, 3)}"] += 1
        if t.slverr and t.waits:
            self.hits[f"{kind}_slverr_waited"] += 1

    def bins(self):
        b = [f"{k}_reg{i}" for k in ("wr", "rd") for i in range(self.n)]
        b += [f"{k}_{e}" for k in ("wr", "rd") for e in ("unaligned", "out_of_range")]
        b += [f"reg{i}:strb_{s:04b}" for i in range(self.n) for s in range(16)]
        b += [f"{k}_wait{w}" for k in ("wr", "rd") for w in range(4)]
        b += [f"{k}_slverr_waited" for k in ("wr", "rd")]
        return b

    def report(self):
        bins = self.bins()
        hit = [x for x in bins if self.hits[x]]
        return 100.0 * len(hit) / len(bins), [x for x in bins if not self.hits[x]]


def random_txn(nregs=8):
    r = random.random()
    if r < 0.85:
        addr = random.randrange(nregs) * 4
    elif r < 0.93:
        addr = random.randrange(nregs * 4, 256) & ~3
    else:
        addr = random.randrange(256) | random.choice([1, 2, 3])
    return ApbTxn(
        write=random.random() < 0.5,
        addr=addr,
        data=random.getrandbits(32),
        strb=random.randrange(16),
    )
