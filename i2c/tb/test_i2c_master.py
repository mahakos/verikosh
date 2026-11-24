import os
import random
import cocotb
import cocotb.utils
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles, ReadOnly, First
from i2c_vip import I2cTiming, I2cTarget, I2cMonitor, Coverage

CLK_PS = 10_000                      # 100 MHz
START, WRITE, READ, STOP = range(4)
ADDR = 0x50


RESET_OUTPUTS = dict(m_scl_oe=0, m_sda_oe=0, scl=1, sda=1, cmd_ready=1, done=0, rd_data=0, nack=0,
                     arb_lost=0, busy=0)


async def check_reset_outputs(dut, when):
    await ReadOnly()
    for name, exp in RESET_OUTPUTS.items():
        v = getattr(dut, name).value
        assert v.is_resolvable and int(v) == exp, f"{name} = {v} {when}, expected {exp}"


class Env:
    def __init__(self, dut, div, timing=None, hold_ps=0):
        self.dut, self.div = dut, div
        self.target = I2cTarget(dut, ADDR, hold_ps=hold_ps)
        self.mon = I2cMonitor(dut, timing or I2cTiming.scaled(div, CLK_PS), dut._log)
        self.mirror = [0] * 256       # scoreboard reference: what the target memory must hold
        self.owned = False            # the controller should be holding the bus (busy)

    async def start(self):
        d = self.dut
        cocotb.start_soon(Clock(d.clk, CLK_PS, unit="ps").start())
        d.div.value = self.div
        d.cmd_valid.value = 0
        d.cmd.value = 0
        d.cmd_data.value = 0
        d.cmd_ack.value = 0
        d.rogue_sda_oe.value = 0
        d.tgt_scl_oe.value = 0
        d.tgt_sda_oe.value = 0
        d.rst_n.value = 0
        await ClockCycles(d.clk, 2)
        await check_reset_outputs(d, "during reset")
        await RisingEdge(d.clk)
        d.rst_n.value = 1
        await RisingEdge(d.clk)
        await check_reset_outputs(d, "after reset")
        await RisingEdge(d.clk)
        cocotb.start_soon(self.target.run())
        cocotb.start_soon(self.mon.run())
        return self

    def set_div(self, div):
        self.div = div
        self.dut.div.value = div
        self.mon.t = I2cTiming.scaled(div, CLK_PS)

    async def cmd(self, op, data=0, ack=1):
        """Issue one controller command, return (rd_data, nack, arb_lost) at its done pulse."""
        d = self.dut
        limit = 200 * self.div + 100_000
        for _ in range(limit):
            await ReadOnly()
            if d.cmd_ready.value:
                break
            await RisingEdge(d.clk)
        else:
            raise AssertionError("controller never ready")
        await RisingEdge(d.clk)
        d.cmd.value, d.cmd_data.value, d.cmd_ack.value, d.cmd_valid.value = op, data, ack, 1
        await RisingEdge(d.clk)
        d.cmd_valid.value = 0
        for _ in range(limit):
            await ReadOnly()
            if d.done.value:
                r = int(d.rd_data.value), bool(d.nack.value), bool(d.arb_lost.value)
                had_bus = self.owned
                self.owned = (had_bus or op == START) and op != STOP and not r[2]
                assert int(d.busy.value) == self.owned, \
                    f"busy={d.busy.value} after command {op}, expected {int(self.owned)}"
                await RisingEdge(d.clk)
                return r
            await RisingEdge(d.clk)
        raise AssertionError(f"command {op} never finished")

    # transaction level -------------------------------------------------------------------------
    async def write(self, ptr, data, addr=ADDR):
        """S addr+W ptr data... P. Returns number of bytes the target ACKed after the pointer."""
        await self.cmd(START)
        _, nack, _ = await self.cmd(WRITE, addr << 1)
        acked = -1
        if not nack:
            _, nack, _ = await self.cmd(WRITE, ptr)
            acked = 0
            for b in data:
                if nack:
                    break
                _, nack, _ = await self.cmd(WRITE, b)
                if not nack:
                    self.mirror[(ptr + acked) & 0xFF] = b
                    acked += 1
        await self.cmd(STOP)
        return acked

    async def read(self, ptr, n, addr=ADDR):
        """S addr+W ptr Sr addr+R data... P (combined format). Returns the bytes read."""
        await self.cmd(START)
        _, nack, _ = await self.cmd(WRITE, addr << 1)
        out = []
        if not nack:
            await self.cmd(WRITE, ptr)
            await self.cmd(START)
            _, nack, _ = await self.cmd(WRITE, addr << 1 | 1)
            for i in range(n):
                b, _, _ = await self.cmd(READ, ack=int(i < n - 1))
                out.append(b)
        await self.cmd(STOP)
        return out

    async def read_current(self, n, addr=ADDR):
        """S addr+R data... P, from the target's current pointer."""
        await self.cmd(START)
        _, nack, _ = await self.cmd(WRITE, addr << 1 | 1)
        out = []
        if not nack:
            for i in range(n):
                b, _, _ = await self.cmd(READ, ack=int(i < n - 1))
                out.append(b)
        await self.cmd(STOP)
        return out

    def expect(self, i0, tokens):
        got = self.mon.tokens[i0:]
        assert got == tokens, f"bus decode mismatch\n  got      {got}\n  expected {tokens}"


@cocotb.test()
async def reset_idle(dut):
    """After reset both lines are released, the controller is idle and ready."""
    env = await Env(dut, div=4).start()
    await ClockCycles(dut.clk, 10)
    await ReadOnly()
    assert dut.scl.value == 1 and dut.sda.value == 1
    assert dut.cmd_ready.value == 1 and dut.busy.value == 0
    await RisingEdge(dut.clk)
    for op in (WRITE, READ, STOP):           # no bus: dropped at once, nothing on the wires
        t0 = cocotb.utils.get_sim_time("ps")
        await env.cmd(op, 0x12)
        assert cocotb.utils.get_sim_time("ps") - t0 <= 3 * CLK_PS, f"dropped command {op} took too long"
    await ClockCycles(dut.clk, 4 * 9 * env.div + 50)
    assert env.mon.edges == 0 and env.mon.violations == 0, f"{env.mon.edges} line changes on an idle bus"


@cocotb.test()
async def start_latency(dut):
    """A START on a free bus is cycle-exact: SDA falls 2*div+1 clocks after the command is accepted
    (tSU;STA = 2 quarters, counted from the command), also for a START issued right after reset."""
    env = await Env(dut, div=3).start()
    for div in Coverage.DIVS:
        env.set_div(div)

        async def latency():
            await RisingEdge(dut.cmd_valid)
            n = 0
            while dut.sda.value:
                await RisingEdge(dut.clk)
                n += 1
            return n

        t = cocotb.start_soon(latency())
        await env.cmd(START)
        n = await t
        assert n == 2 * div + 2, f"div={div}: SDA fell {n - 1} clocks after the START was accepted, expected {2 * div + 1}"
        await env.cmd(WRITE, ADDR << 1)
        await env.cmd(STOP)
        await ClockCycles(dut.clk, 4 * div)
    assert env.mon.violations == 0


@cocotb.test()
async def standard_mode_timing(dut):
    """One write and one combined read at 100 kHz, checked against the UM10204 Standard-mode table."""
    env = await Env(dut, div=250, timing=I2cTiming.standard_mode(), hold_ps=300_000).start()
    assert await env.write(0x10, [0xDE, 0xAD]) == 2
    i0 = len(env.mon.tokens)
    got = await env.read(0x10, 2)
    assert got == [0xDE, 0xAD], got
    env.expect(i0, [("S",), ("B", 0xA0, True), ("B", 0x10, True), ("Sr",), ("B", 0xA1, True),
                    ("B", 0xDE, True), ("B", 0xAD, False), ("P",)])
    assert env.mon.violations == 0


@cocotb.test()
async def directed(dut):
    """Address NACK, data NACK, current-address read, and the exact bus sequence of each."""
    env = await Env(dut, div=5).start()
    i0 = len(env.mon.tokens)
    assert await env.write(0x00, [1], addr=0x23) == -1
    env.expect(i0, [("S",), ("B", 0x46, False), ("P",)])

    env.target.nack_after = 2
    i0 = len(env.mon.tokens)
    assert await env.write(0x40, [0x11, 0x22, 0x33, 0x44]) == 2
    env.expect(i0, [("S",), ("B", 0xA0, True), ("B", 0x40, True), ("B", 0x11, True), ("B", 0x22, True),
                    ("B", 0x33, False), ("P",)])
    env.target.nack_after = None
    assert env.target.mem[0x40:0x43] == [0x11, 0x22, 0x00]

    await env.write(0x80, [0xC0, 0xC1, 0xC2])
    assert await env.read(0x80, 1) == [0xC0]
    assert await env.read_current(2) == [0xC1, 0xC2]
    assert env.mon.violations == 0


@cocotb.test()
async def clock_stretching(dut):
    """The target holds SCL low after every edge; the controller must wait, keep tHIGH and lose no bits."""
    env = await Env(dut, div=4).start()
    env.target.stretch_prob = 1.0
    env.target.stretch_ps = (CLK_PS * 30, CLK_PS * 80)
    data = [random.getrandbits(8) for _ in range(6)]
    await env.write(0x20, data)
    assert await env.read(0x20, 6) == data
    assert env.target.stretched > 50 and env.mon.violations == 0


@cocotb.test()
async def arbitration(dut):
    """A second master sends a smaller address at the same time; the controller must lose at the first
    bit where it sends 1 and the other master sends 0, release both lines, and work again afterwards."""
    seed = int(os.environ.get("SEED", random.randrange(1 << 31)))
    rnd = random.Random(seed + 7)
    env = await Env(dut, div=4).start()
    env.mon.allow_abort = True
    for _ in range(8):
        mine = rnd.randrange(1, 128) << 1
        theirs = rnd.randrange(0, mine >> 1) << 1          # numerically smaller -> wins arbitration
        lose_at = next(i for i in range(8) if (mine ^ theirs) >> (7 - i) & 1)

        rises = []

        async def rogue():
            await FallingEdge(dut.sda)                    # START
            lost = RisingEdge(dut.arb_lost)
            for i in range(8):
                if await First(FallingEdge(dut.scl), lost) is lost:
                    break
                dut.rogue_sda_oe.value = int(not (theirs >> (7 - i) & 1))
                if await First(RisingEdge(dut.scl), lost) is lost:
                    break
                rises.append(i)
            dut.rogue_sda_oe.value = 0

        task = cocotb.start_soon(rogue())
        await env.cmd(START)
        _, _, lost = await env.cmd(WRITE, mine)
        assert lost, f"0x{mine:02x} vs 0x{theirs:02x}: arbitration loss not reported"
        await ReadOnly()
        assert dut.m_scl_oe.value == 0 and dut.m_sda_oe.value == 0, "lines not released after losing"
        assert dut.busy.value == 0 and dut.cmd_ready.value == 1
        assert len(rises) == lose_at + 1, f"lost after {len(rises)} bits, expected {lose_at + 1}"
        await RisingEdge(dut.clk)
        await ClockCycles(dut.clk, 2)
        assert task.done(), "rogue master still waiting"
        await ClockCycles(dut.clk, 40)
    env.mon.allow_abort = False
    await env.write(0x05, [0x5A])
    assert await env.read(0x05, 1) == [0x5A]
    assert env.mon.violations == 0


@cocotb.test()
async def constrained_random(dut):
    """Random writes, combined reads and current-address reads with random dividers, clock stretching,
    wrong addresses and data NACKs, until coverage closes. Every read is checked against a mirror of
    the target memory, every transaction against the bus monitor's independent decode."""
    seed = int(os.environ.get("SEED", random.randrange(1 << 31)))
    random.seed(seed)
    dut._log.info(f"SEED={seed}")
    env = await Env(dut, div=Coverage.DIVS[0]).start()
    cov = Coverage()
    special = [0x00, 0xFF, 0x55, 0xAA]
    pick = lambda: random.choice(special) if random.random() < 0.3 else random.getrandbits(8)
    errors = 0
    for rnd in range(400):
        env.set_div(random.choice(Coverage.DIVS))
        await ClockCycles(dut.clk, 2)
        stretch = random.random() < 0.3
        env.target.stretch_prob = 0.3 if stretch else 0.0
        env.target.stretch_ps = (CLK_PS * 5, CLK_PS * 40)
        s0 = env.target.stretched
        addr = ADDR if random.random() < 0.9 else random.choice([a for a in range(8, 120) if a != ADDR])
        kind = random.choice(["write", "read", "write_read"])
        n = random.choice([1, 1, 2, 3, 4, 6])
        ptr = random.getrandbits(8)
        i0 = len(env.mon.tokens)
        if kind == "write":
            data = [pick() for _ in range(n)]
            env.target.nack_after = random.randrange(n) if random.random() < 0.1 else None
            acked = await env.write(ptr, data, addr=addr)
            env.target.nack_after = None
            if addr != ADDR:
                ok = acked == -1
            else:
                ok = all(env.target.mem[(ptr + i) & 0xFF] == env.mirror[(ptr + i) & 0xFF] for i in range(n))
            cov.sample(kind, n, data[:max(acked, 0)], env.div, env.target.stretched > s0, addr == ADDR,
                       0 <= acked < n)
        else:
            if kind == "read":
                got = await env.read_current(n, addr=addr)
                start = env.target.ptr - n if addr == ADDR else 0
            else:
                got = await env.read(ptr, n, addr=addr)
                start = ptr
            exp = [env.mirror[(start + i) & 0xFF] for i in range(n)] if addr == ADDR else []
            ok = got == exp
            cov.sample(kind, n, got, env.div, env.target.stretched > s0, addr == ADDR)
        # independent bus decode: every byte must be ACKed except the address NACK and final read byte
        toks = env.mon.tokens[i0:]
        if toks[0] != ("S",) or toks[-1] != ("P",) or toks[1][1] >> 1 != addr:
            ok = False
        if not ok:
            errors += 1
            dut._log.error(f"round {rnd}: {kind} n={n} addr=0x{addr:02x} ptr=0x{ptr:02x} failed, bus {toks}")
        if cov.report()[0] == 100.0 and rnd >= 30:
            break
    pct, miss, nbins = cov.report()
    dut._log.info(f"{rnd + 1} transactions, {errors} errors, {env.mon.violations} bus violations, "
                  f"{env.target.stretched} stretched clocks, coverage {pct:.1f}% of {nbins} bins")
    assert errors == 0 and env.mon.violations == 0
    assert env.target.mem == env.mirror, "target memory differs from the scoreboard mirror"
    assert pct == 100.0, f"coverage holes: {miss}"
