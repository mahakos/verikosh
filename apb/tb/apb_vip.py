"""VeriKosh APB VIP (cocotb): master driver, passive monitor, reference model, scoreboard, coverage."""
import random
from dataclasses import dataclass
from collections import Counter
from cocotb.triggers import RisingEdge, ReadOnly


@dataclass
class ApbTxn:
    write: bool
    addr: int
    data: int = 0
    strb: int = 0xF
    slverr: bool = False


class ApbMaster:
    """Drives APB3 setup + access phases and waits for PREADY."""

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
        while True:
            await ReadOnly()
            if d.pready.value:
                break
            await RisingEdge(self.clk)
        if not t.write:
            t.data = int(d.prdata.value)
        t.slverr = bool(d.pslverr.value)
        await RisingEdge(self.clk)
        d.psel.value, d.penable.value = 0, 0
        return t


class ApbMonitor:
    """Passive monitor: samples every completed access and calls subscribers."""

    def __init__(self, dut, clk, callbacks):
        self.dut, self.clk, self.cbs = dut, clk, callbacks

    async def run(self):
        d = self.dut
        while True:
            await RisingEdge(self.clk)
            await ReadOnly()
            if d.psel.value and d.penable.value and d.pready.value:
                t = ApbTxn(
                    write=bool(d.pwrite.value),
                    addr=int(d.paddr.value),
                    data=int(d.pwdata.value) if d.pwrite.value else int(d.prdata.value),
                    strb=int(d.pstrb.value),
                    slverr=bool(d.pslverr.value),
                )
                for cb in self.cbs:
                    cb(t)


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
    """Lightweight functional coverage: direction x register, strobes, error cases."""

    def __init__(self, nregs=8):
        self.n = nregs
        self.hits = Counter()

    def __call__(self, t: ApbTxn):
        kind = "wr" if t.write else "rd"
        if t.addr % 4 == 0 and t.addr // 4 < self.n:
            self.hits[f"{kind}_reg{t.addr // 4}"] += 1
        elif t.addr % 4:
            self.hits[f"{kind}_unaligned"] += 1
        else:
            self.hits[f"{kind}_out_of_range"] += 1
        if t.write:
            self.hits[f"strb_{t.strb:04b}"] += 1

    def bins(self):
        b = [f"{k}_reg{i}" for k in ("wr", "rd") for i in range(self.n)]
        b += [f"{k}_{e}" for k in ("wr", "rd") for e in ("unaligned", "out_of_range")]
        b += [f"strb_{s:04b}" for s in range(16)]
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
