import os
import random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge
from apb_vip import (ApbMaster, ApbMonitor, ApbProtocolChecker, ApbTxn, RegModel, Scoreboard,
                     Coverage, random_txn)

NREGS = 8


async def setup(dut):
    clk = dut.pclk
    cocotb.start_soon(Clock(clk, 10, unit="ns").start())
    master = ApbMaster(dut, clk)
    model, cov = RegModel(NREGS), Coverage(NREGS)
    sb = Scoreboard(model, dut._log)
    proto = ApbProtocolChecker(dut, clk, dut._log)
    cocotb.start_soon(ApbMonitor(dut, clk, [sb, cov]).run())
    cocotb.start_soon(proto.run())
    dut.presetn.value = 0
    for _ in range(3):
        await RisingEdge(clk)
    dut.presetn.value = 1
    master.proto = proto
    return master, sb, cov


@cocotb.test()
async def reset_values(dut):
    """Every register reads 0 after reset, before anything is written."""
    master, sb, _ = await setup(dut)
    for i in range(NREGS):
        t = await master.xfer(ApbTxn(False, i * 4))
        assert t.data == 0 and not t.slverr, f"reg{i} = 0x{t.data:08x} after reset"
    await RisingEdge(dut.pclk)
    assert sb.errors == 0 and master.proto.violations == 0


@cocotb.test()
async def directed_write_read(dut):
    """Write then read back every register with full strobes."""
    master, sb, _ = await setup(dut)
    for i in range(NREGS):
        await master.xfer(ApbTxn(True, i * 4, 0xA5A50000 | i))
    for i in range(NREGS):
        t = await master.xfer(ApbTxn(False, i * 4))
        assert t.data == 0xA5A50000 | i
    await RisingEdge(dut.pclk)
    assert sb.errors == 0 and sb.checked == 2 * NREGS and master.proto.violations == 0


@cocotb.test()
async def error_responses(dut):
    """Out-of-range and unaligned accesses must return PSLVERR and not change state."""
    master, sb, _ = await setup(dut)
    for a in (0x20, 0xFC, 0x01, 0x06):
        t = await master.xfer(ApbTxn(True, a, 0xDEADBEEF))
        assert t.slverr, f"no PSLVERR at 0x{a:02x}"
    for i in range(NREGS):                     # nothing was written by the failed accesses
        assert (await master.xfer(ApbTxn(False, i * 4))).data == 0
    await RisingEdge(dut.pclk)
    assert sb.errors == 0 and master.proto.violations == 0


@cocotb.test()
async def constrained_random(dut):
    """Constrained-random traffic until functional coverage is closed."""
    seed = int(os.environ.get("SEED", random.randrange(1 << 31)))
    random.seed(seed)
    dut._log.info(f"SEED={seed}")
    master, sb, cov = await setup(dut)
    n = 0
    while n < 20000:
        await master.xfer(random_txn(NREGS))
        n += 1
        if n % 100 == 0 and cov.report()[0] == 100.0:
            break
    await RisingEdge(dut.pclk)
    pct, missing = cov.report()
    dut._log.info(f"{n} transactions, {sb.checked} checked, {sb.errors} errors, coverage {pct:.1f}%")
    assert sb.errors == 0 and master.proto.violations == 0
    assert pct == 100.0, f"coverage holes: {missing}"
