import os
import random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, ReadOnly
from axil_vip import (AxilMaster, AxilReadyDriver, AxilMonitor, ProtocolChecker,
                      RegModel, Scoreboard, Coverage, OKAY, SLVERR)

NREGS = 16
VERSION = 0x4E450001
RO_ADDR = (NREGS - 1) * 4


async def setup(dut, p_ready=1.0, max_delay=3):
    clk = dut.aclk
    cocotb.start_soon(Clock(clk, 10, unit="ns").start())
    master = AxilMaster(dut, clk, max_delay)
    ready = AxilReadyDriver(dut, clk, p_ready)
    model, cov = RegModel(NREGS, VERSION), Coverage(NREGS)
    sb = Scoreboard(model, dut._log)
    proto = ProtocolChecker(dut, clk, dut._log)
    dut.aresetn.value = 0
    await ClockCycles(clk, 3)
    dut.aresetn.value = 1
    cocotb.start_soon(AxilMonitor(dut, clk, [sb, cov]).run())
    cocotb.start_soon(proto.run())
    if p_ready < 1.0:
        cocotb.start_soon(ready.run())
    return master, sb, cov, proto


async def finish(dut, sb, proto):
    await ClockCycles(dut.aclk, 4)
    assert proto.violations == 0, f"{proto.violations} AXI protocol violations"
    assert sb.errors == 0, f"{sb.errors} scoreboard errors"


RESET_OUTPUTS = dict(awready=1, wready=1, arready=1, bvalid=0, bresp=0, rvalid=0, rresp=0, rdata=0)


@cocotb.test()
async def reset_values(dut):
    """Out of reset every output has its documented value (no X), every RW register reads 0 and the
    VERSION register reads its constant."""
    master, sb, _, proto = await setup(dut)
    await ReadOnly()
    for name, exp in RESET_OUTPUTS.items():
        v = getattr(dut, name).value
        assert v.is_resolvable and int(v) == exp, f"{name} = {v} after reset, expected {exp}"
    await RisingEdge(dut.aclk)
    for i in range(NREGS - 1):
        data, resp = await master.read(i * 4)
        assert (data, resp) == (0, OKAY), f"reg{i} = 0x{data:08x} after reset"
    assert await master.read(RO_ADDR) == (VERSION, OKAY)
    await finish(dut, sb, proto)


@cocotb.test()
async def valid_before_ready(dut):
    """AXI dependency rule (AMBA AXI A3.3.1): the slave must not wait for BREADY before asserting BVALID,
    nor for RREADY before asserting RVALID. Both READYs are held low; the responses must still appear,
    stay up until accepted, and then complete normally."""
    master, sb, _, proto = await setup(dut)
    d = dut
    d.bready.value = 0
    d.rready.value = 0
    await RisingEdge(d.aclk)
    aw = cocotb.start_soon(master._channel(d.awvalid, d.awready, [(d.awaddr, 0x8)], 0))
    w = cocotb.start_soon(master._channel(d.wvalid, d.wready, [(d.wdata, 0xA5A5_0003), (d.wstrb, 0xF)], 0))
    await aw
    await w
    for name in ("bvalid", "rvalid"):
        if name == "rvalid":
            await master._channel(d.arvalid, d.arready, [(d.araddr, 0x8)], 0)
        for _ in range(4):
            await ReadOnly()
            if getattr(d, name).value:
                break
            await RisingEdge(d.aclk)
        else:
            raise AssertionError(f"{name.upper()} waited for {name[0].upper()}READY")
        await ClockCycles(d.aclk, 5)
        await ReadOnly()
        assert getattr(d, name).value == 1, f"{name.upper()} dropped before it was accepted"
        await RisingEdge(d.aclk)
    assert int(d.bresp.value) == OKAY
    assert (int(d.rdata.value), int(d.rresp.value)) == (0xA5A5_0003, OKAY)
    d.bready.value = 1
    d.rready.value = 1
    await ClockCycles(d.aclk, 2)
    await ReadOnly()
    assert d.bvalid.value == 0 and d.rvalid.value == 0
    await RisingEdge(d.aclk)
    await finish(dut, sb, proto)


@cocotb.test()
async def directed_access(dut):
    """Full-word and byte-strobe writes, read-only register and out-of-range errors."""
    master, sb, _, proto = await setup(dut)
    for i in range(NREGS - 1):
        assert await master.write(i * 4, 0xC0DE0000 | i) == OKAY
    for i in range(NREGS - 1):
        assert await master.read(i * 4) == (0xC0DE0000 | i, OKAY)
    # byte lanes
    await master.write(0x0, 0x11223344, 0b0101)
    assert (await master.read(0x0))[0] == 0xC0220044
    # read-only register rejects writes and keeps its value
    assert await master.write(RO_ADDR, 0xFFFFFFFF) == SLVERR
    assert await master.read(RO_ADDR) == (VERSION, OKAY)
    # out of range
    assert await master.write(0x40, 0x1) == SLVERR
    assert await master.read(0xFC) == (0, SLVERR)
    await finish(dut, sb, proto)


@cocotb.test()
async def channel_ordering(dut):
    """AW before W, W before AW, and both in the same cycle must all work."""
    master, sb, cov, proto = await setup(dut)
    for aw_d, w_d in ((0, 3), (3, 0), (0, 0), (2, 2)):
        await master.write(0x8, 0x1000 * aw_d + w_d, aw_delay=aw_d, w_delay=w_d)
        assert await master.read(0x8) == (0x1000 * aw_d + w_d, OKAY)
    for o in ("aw_first", "w_first", "same"):
        assert cov.hits[f"order:{o}:bp0"], f"ordering {o} not observed"
    await finish(dut, sb, proto)


@cocotb.test()
async def constrained_random(dut):
    """Random traffic with random BREADY/RREADY back-pressure until coverage closes."""
    seed = int(os.environ.get("SEED", random.randrange(1 << 31)))
    random.seed(seed)
    dut._log.info(f"SEED={seed}")
    master, sb, cov, proto = await setup(dut, p_ready=0.5, max_delay=3)
    n = 0
    while n < 20000:
        r = random.random()
        if r < 0.8:
            addr = random.randrange(NREGS - 1) * 4
        elif r < 0.9:
            addr = RO_ADDR
        else:
            addr = random.randrange(NREGS, 64) * 4
        if random.random() < 0.5:
            await master.write(addr, random.getrandbits(32), random.randrange(16))
        else:
            await master.read(addr)
        n += 1
        if n % 50 == 0 and cov.report()[0] == 100.0:
            break
    await finish(dut, sb, proto)
    pct, miss, nbins = cov.report()
    dut._log.info(f"{n} transactions, {sb.checked} checked, {sb.errors} errors, "
                  f"coverage {pct:.1f}% of {nbins} bins")
    assert pct == 100.0, f"coverage holes: {miss}"
