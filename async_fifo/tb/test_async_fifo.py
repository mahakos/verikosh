import os
import random
import cocotb
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge, Timer
from afifo_vip import (JitterClock, Shared, Writer, Reader, FlagChecker, GrayChecker, Coverage,
                       ratio_bin)

SYNC_STAGES = 2


class Env:
    def __init__(self, dut, wper=10_000, rper=13_000, seed=None):
        self.dut = dut
        seed = int(os.environ.get("SEED", random.randrange(1 << 31))) if seed is None else seed
        dut._log.info(f"SEED={seed}")
        self.rng = random.Random(seed)
        self.depth = 1 << int(dut.AW.value)
        self.sh = Shared(self.depth)
        self.wclk = JitterClock(dut.wclk, wper, rng=self.rng)
        self.rclk = JitterClock(dut.rclk, rper, rng=self.rng)
        self.writer = Writer(dut, self.sh, self.rng)
        self.reader = Reader(dut, self.sh, self.rng, dut._log)
        self.flags = FlagChecker(self.sh, SYNC_STAGES, dut._log)
        self.gray = GrayChecker(dut, dut._log)
        self.cov = Coverage()
        self.ratio = ratio_bin(wper, rper)

    async def start(self):
        d = self.dut
        d.wrst_n.value = 0
        d.rrst_n.value = 0
        d.winc.value = 0
        d.rinc.value = 0
        cocotb.start_soon(self.wclk.run())
        cocotb.start_soon(self.rclk.run())
        await ClockCycles(d.wclk, 4)
        await ClockCycles(d.rclk, 4)
        await ReadOnly()
        assert d.rempty.value == 1 and d.wfull.value == 0, "flags wrong while in reset"
        await Timer(1, unit="ps")
        d.wrst_n.value = 1
        d.rrst_n.value = 1
        self.gray.start()
        cocotb.start_soon(self.writer.run(self._w_cycle))
        cocotb.start_soon(self.reader.run(self._r_cycle))
        return self

    def _w_cycle(self, full):
        self.flags.write_side(full)
        self.cov.sample_occ(self.sh.occupancy, self.depth)
        if full:
            self.cov.hits[f"{self.ratio}:full"] += 1
        if self.writer.blocked:
            self.cov.hits["blocked_push"] += 1

    def _r_cycle(self, empty):
        self.flags.read_side(empty)
        if empty and self.sh.pops:
            self.cov.hits[f"{self.ratio}:empty"] += 1
        if self.reader.blocked:
            self.cov.hits["blocked_pop"] += 1
        if self.sh.pops >= 2 * 2 * self.depth:
            self.cov.hits["wrap2"] += 1

    def set_clocks(self, wper, rper):
        self.wclk.period, self.rclk.period = wper, rper
        self.ratio = ratio_bin(wper, rper)

    def check(self):
        assert self.reader.errors == 0, f"{self.reader.errors} data errors"
        assert self.flags.violations == 0, f"{self.flags.violations} flag violations"
        assert self.gray.violations == 0, f"{self.gray.violations} Gray-code violations"


@cocotb.test()
async def reset_flags(dut):
    """Out of reset the FIFO is empty and not full, and stays so with no traffic."""
    env = await Env(dut).start()
    env.writer.enabled = env.reader.enabled = False
    for _ in range(20):
        await RisingEdge(dut.rclk)
        await ReadOnly()
        assert dut.rempty.value == 1 and dut.wfull.value == 0
    env.check()


@cocotb.test()
async def fill_and_drain(dut):
    """With the reader stopped exactly DEPTH pushes are accepted, extra pushes are refused, and draining
    returns every word in order. Done for a fast and for a slow write clock."""
    env = await Env(dut).start()
    env.writer.p_write = env.reader.p_read = 1.0
    for wper, rper in ((7_000, 23_000), (23_000, 7_000)):
        env.set_clocks(wper, rper)
        p0, c0, b0 = env.sh.pushes, env.reader.checked, env.writer.blocked
        env.reader.enabled, env.writer.enabled = False, True
        await ClockCycles(dut.wclk, 4 * env.depth)
        assert env.sh.pushes - p0 == env.depth, f"accepted {env.sh.pushes - p0} pushes into a depth-{env.depth} FIFO"
        assert env.writer.blocked > b0
        env.reader.enabled, env.writer.enabled = True, False
        await ClockCycles(dut.rclk, 4 * env.depth)
        assert env.reader.checked - c0 == env.depth and not env.sh.model
        await ReadOnly()
        assert dut.rempty.value == 1
        await RisingEdge(dut.rclk)
    env.check()


@cocotb.test()
async def cdc_random(dut):
    """Random clock ratios (with jitter, changed on the fly) and random bursty traffic on both sides,
    including attempted pushes when full and pops when empty, until coverage closes."""
    env = await Env(dut).start()
    rng = env.rng
    plan = [(5_000, 17_000), (9_000, 13_000), (10_000, 10_300), (13_000, 9_000), (19_000, 6_000)]
    for phase in range(60):
        wper, rper = plan[phase] if phase < len(plan) else (rng.randint(4_000, 25_000), rng.randint(4_000, 25_000))
        env.set_clocks(wper, rper)
        for _ in range(4):
            env.writer.p_write = rng.choice([0.1, 0.3, 0.6, 0.9, 1.0])
            env.reader.p_read = rng.choice([0.1, 0.3, 0.6, 0.9, 1.0])
            await Timer(rng.randint(30, 120) * max(wper, rper) // 1, unit="ps")
        if env.cov.report()[0] == 100.0 and phase >= 20:
            break
    env.writer.enabled = False
    env.reader.p_read = 1.0
    await ClockCycles(dut.rclk, 8 * env.depth)
    pct, miss, nbins = env.cov.report()
    dut._log.info(f"{env.sh.pushes} pushes, {env.reader.checked} pops checked, "
                  f"{env.writer.blocked} blocked pushes, {env.reader.blocked} blocked pops, "
                  f"flag release max {env.flags.max_stale_full} wclk / {env.flags.max_stale_empty} rclk, "
                  f"coverage {pct:.1f}% of {nbins} bins")
    env.check()
    assert not env.sh.model, f"{len(env.sh.model)} words never came out"
    assert pct == 100.0, f"coverage holes: {miss}"


async def _hold_flags(dut, clk, cycles):
    for _ in range(cycles):
        await RisingEdge(clk)
        await ReadOnly()
        assert dut.rempty.value == 1, "rempty dropped with no data written"
        assert dut.wfull.value == 0, "wfull rose with no data written"


@cocotb.test()
async def staggered_resets(dut):
    """The two resets come from different clock domains, so they are often released at
    different times. With no traffic the flags must stay empty / not full in every order,
    including while the other domain is still held in reset (its pointer must already read 0)."""
    for first in ("read", "write"):
        rng = random.Random(int(os.environ.get("SEED", 1)))
        wclk = JitterClock(dut.wclk, 10_000, rng=rng)
        rclk = JitterClock(dut.rclk, 7_000, rng=rng)
        dut.wrst_n.value = 0
        dut.rrst_n.value = 0
        dut.winc.value = 0
        dut.rinc.value = 0
        tw = cocotb.start_soon(wclk.run())
        tr = cocotb.start_soon(rclk.run())
        await ClockCycles(dut.wclk, 4)
        await ClockCycles(dut.rclk, 4)
        await Timer(1, unit="ps")
        if first == "read":
            dut.rrst_n.value = 1
            await _hold_flags(dut, dut.rclk, 12)
            await Timer(1, unit="ps")
            dut.wrst_n.value = 1
        else:
            dut.wrst_n.value = 1
            await _hold_flags(dut, dut.wclk, 12)
            await Timer(1, unit="ps")
            dut.rrst_n.value = 1
        await _hold_flags(dut, dut.rclk, 12)
        tw.cancel()
        tr.cancel()
        await Timer(20_000, unit="ps")
