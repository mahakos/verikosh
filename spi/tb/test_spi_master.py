import os
import random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, ReadOnly
from spi_vip import SpiConfig, SpiSlave, SpiChecker, Coverage

CLK_PS = 10_000
TIMEOUT = 400              # cycles; one frame at div=5 takes about 100


def apply(dut, cfg):
    dut.cpol.value, dut.cpha.value = cfg.cpol, cfg.cpha
    dut.lsb_first.value, dut.div.value = cfg.lsb_first, cfg.div


async def setup(dut, cfg):
    cocotb.start_soon(Clock(dut.clk, CLK_PS, unit="ps").start())
    dut.tx_valid.value = 0
    dut.tx_data.value = 0
    apply(dut, cfg)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)
    slave = SpiSlave(dut, cfg)
    checker = SpiChecker(dut, cfg, CLK_PS, dut._log)
    cocotb.start_soon(slave.run())
    cocotb.start_soon(checker.run())
    return slave, checker


async def transfer(dut, data, keep_valid=False):
    """Hand one byte to the master and return the byte it received."""
    dut.tx_data.value = data
    dut.tx_valid.value = 1
    for _ in range(TIMEOUT):
        await ReadOnly()
        accepted = bool(dut.tx_ready.value)
        await RisingEdge(dut.clk)
        if accepted:
            break
    else:
        raise AssertionError("master never became ready")
    if not keep_valid:
        dut.tx_valid.value = 0
    for _ in range(TIMEOUT):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if dut.rx_valid.value:
            rx = int(dut.rx_data.value)
            await RisingEdge(dut.clk)
            return rx
    raise AssertionError("no rx_valid: frame never completed")


@cocotb.test()
async def reset_idle(dut):
    """After reset CS_N is high, SCLK sits at CPOL (and follows it while idle), master is ready."""
    cfg = SpiConfig(cpol=0)
    _, checker = await setup(dut, cfg)
    for cpol in (0, 1, 0):
        cfg.cpol = cpol
        apply(dut, cfg)
        await ClockCycles(dut.clk, 4)
        await ReadOnly()
        assert dut.cs_n.value == 1 and dut.tx_ready.value == 1
        assert int(dut.sclk.value) == cpol, f"SCLK={dut.sclk.value} with CPOL={cpol}"
        await RisingEdge(dut.clk)
    assert checker.violations == 0 and checker.frames == 0


@cocotb.test()
async def directed_all_modes(dut):
    """One full-duplex exchange in each of the 8 mode x bit-order combinations."""
    cfg = SpiConfig(div=2)
    slave, checker = await setup(dut, cfg)
    for mode in range(4):
        for lsb in (0, 1):
            cfg.cpol, cfg.cpha, cfg.lsb_first = mode >> 1, mode & 1, lsb
            apply(dut, cfg)
            await ClockCycles(dut.clk, 4)
            slave.replies.append(0x3C)
            rx = await transfer(dut, 0xA5)
            assert slave.received[-1] == 0xA5, f"mode {mode} lsb={lsb}: slave got {slave.received[-1]}"
            assert rx == 0x3C, f"mode {mode} lsb={lsb}: master got 0x{rx:02x}"
    assert checker.violations == 0


@cocotb.test()
async def constrained_random(dut):
    """Random modes, dividers, bit order and data, including back-to-back frames."""
    seed = int(os.environ.get("SEED", random.randrange(1 << 31)))
    random.seed(seed)
    dut._log.info(f"SEED={seed}")
    cfg = SpiConfig()
    slave, checker = await setup(dut, cfg)
    cov = Coverage()
    special = [0x00, 0xFF, 0x55, 0xAA]
    pick = lambda: random.choice(special) if random.random() < 0.3 else random.getrandbits(8)
    errors = frames = 0
    for rnd in range(500):
        cfg.cpol, cfg.cpha = random.randrange(2), random.randrange(2)
        cfg.lsb_first, cfg.div = random.randrange(2), random.choice(Coverage.DIVS)
        apply(dut, cfg)
        await ClockCycles(dut.clk, 4)
        burst = random.randint(1, 4)
        for i in range(burst):
            mosi, miso = pick(), pick()
            slave.replies.append(miso)
            rx = await transfer(dut, mosi, keep_valid=(i < burst - 1))
            frames += 1
            if slave.received[-1] != mosi or rx != miso:
                errors += 1
                dut._log.error(f"mode{cfg.mode} lsb={cfg.lsb_first} div={cfg.div}: "
                               f"MOSI 0x{mosi:02x}->{slave.received[-1]} MISO 0x{miso:02x}->0x{rx:02x}")
            cov.sample(cfg, mosi, miso, back_to_back=i > 0)
        dut.tx_valid.value = 0
        if cov.report()[0] == 100.0 and rnd >= 20:
            break
    await ClockCycles(dut.clk, 20)
    pct, miss, nbins = cov.report()
    dut._log.info(f"{frames} frames, {errors} errors, {checker.violations} protocol violations, "
                  f"coverage {pct:.1f}% of {nbins} bins")
    assert errors == 0 and checker.violations == 0
    assert checker.frames == frames, f"checker saw {checker.frames} CS windows for {frames} frames"
    assert pct == 100.0, f"coverage holes: {miss}"
