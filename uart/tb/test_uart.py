import os
import random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer, ReadOnly
from uart_vip import UartConfig, UartSource, UartSink, RxMonitor, Coverage, PARITY

CLK_PS = 10_000
TIMEOUT = 54 * 12 * 2     # cycles: two frames at the slowest divisor


async def setup(dut, cfg):
    cocotb.start_soon(Clock(dut.clk, CLK_PS, unit="ps").start())
    dut.tx_valid.value = 0
    dut.tx_data.value = 0
    dut.rxd.value = 1
    apply(dut, cfg)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)
    tx_seen = []
    sink = UartSink(dut.txd, cfg, [tx_seen.append])
    mon = RxMonitor(dut, dut.clk)
    cocotb.start_soon(sink.run())
    cocotb.start_soon(mon.run())
    return UartSource(dut.rxd, cfg), tx_seen, mon


def apply(dut, cfg):
    dut.div.value = cfg.div
    dut.par_en.value = int(cfg.parity != "none")
    dut.par_odd.value = int(cfg.parity == "odd")


async def tx_byte(dut, b):
    dut.tx_data.value = b
    dut.tx_valid.value = 1
    for _ in range(TIMEOUT):
        await ReadOnly()
        accepted = bool(dut.tx_ready.value)
        await RisingEdge(dut.clk)
        if accepted:
            break
    else:
        raise AssertionError("transmitter never became ready")
    dut.tx_valid.value = 0


async def wait_tx_idle(dut, cfg):
    for _ in range(TIMEOUT):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if dut.tx_ready.value and not dut.tx_valid.value:
            break
    else:
        raise AssertionError("transmitter never returned to idle")
    await Timer(cfg.bit_ps, unit="ps")      # let the sink finish the stop bit


@cocotb.test()
async def reset_state(dut):
    """After reset: line idle high, transmitter ready, no received frames."""
    cfg = UartConfig()
    _, tx_seen, mon = await setup(dut, cfg)
    for _ in range(cfg.div * 12):
        await RisingEdge(dut.clk)
        await ReadOnly()
        assert dut.txd.value == 1 and dut.tx_ready.value == 1 and dut.rx_valid.value == 0
    assert not tx_seen and not mon.frames


@cocotb.test()
async def directed_all_parity_modes(dut):
    """0x55, 0xA3, 0x00, 0xFF in each parity mode, both directions."""
    cfg = UartConfig(div=16)
    src, tx_seen, mon = await setup(dut, cfg)
    pattern = [0x55, 0xA3, 0x00, 0xFF]
    for p in PARITY:
        cfg.parity = p
        apply(dut, cfg)
        tx_seen.clear(); mon.frames.clear()
        for b in pattern:
            await tx_byte(dut, b)
        for b in pattern:
            await src.send(b)
        await wait_tx_idle(dut, cfg)
        await ClockCycles(dut.clk, cfg.div)
        assert [f.data for f in tx_seen] == pattern, f"{p}: TX saw {tx_seen}"
        assert not any(f.perr or f.ferr for f in tx_seen), f"{p}: TX framing/parity wrong"
        assert [(f.data, f.perr, f.ferr) for f in mon.frames] == [(b, False, False) for b in pattern], \
            f"{p}: RX got {mon.frames}"


@cocotb.test()
async def error_injection(dut):
    """Parity errors, framing errors, BREAK and start-bit glitches are flagged or rejected."""
    cfg = UartConfig(div=16, parity="even")
    src, _, mon = await setup(dut, cfg)
    await src.send(0x3C, bad_parity=True)
    await src.send(0x3C)
    await src.send(0x81, bad_stop=True)
    await src.glitch()
    await src.send(0x7E)
    await src.line_break()
    await src.send(0x42)
    await ClockCycles(dut.clk, cfg.div * 2)
    got = [(f.data, f.perr, f.ferr) for f in mon.frames]
    # a BREAK is one all-zero frame with a framing error (parity of 0x00 is 0, so no parity error)
    assert got == [(0x3C, True, False), (0x3C, False, False), (0x81, False, True), (0x7E, False, False),
                   (0x00, False, True), (0x42, False, False)], got


@cocotb.test()
async def constrained_random(dut):
    """Random bytes, divisors, parity modes, ±2% baud skew and injected errors until coverage closes."""
    seed = int(os.environ.get("SEED", random.randrange(1 << 31)))
    random.seed(seed)
    dut._log.info(f"SEED={seed}")
    cfg = UartConfig()
    src, tx_seen, mon = await setup(dut, cfg)
    cov = Coverage()
    errors = frames = 0
    special = [0x00, 0xFF, 0x55, 0xAA]
    for rnd in range(200):
        cfg.div = random.choice(Coverage.DIVS)
        cfg.parity = random.choice(PARITY)
        apply(dut, cfg)
        tx_seen.clear(); mon.frames.clear()
        n = random.randint(2, 5)
        tx_bytes = [random.choice(special) if random.random() < 0.3 else random.getrandbits(8) for _ in range(n)]
        rx_plan = []
        for _ in range(n):
            b = random.choice(special) if random.random() < 0.3 else random.getrandbits(8)
            skew = random.choice([("slow", 0.02), ("nominal", 0.0), ("fast", -0.02)])
            err = random.choices([None, "parity", "framing", "glitch"], [0.7, 0.1, 0.1, 0.1])[0]
            if err == "parity" and cfg.parity == "none":
                err = None
            rx_plan.append((b, skew, err))

        async def drive_tx():
            for b in tx_bytes:
                await tx_byte(dut, b)
            await wait_tx_idle(dut, cfg)

        tx_task = cocotb.start_soon(drive_tx())
        expected_rx = []
        for b, (sname, s), err in rx_plan:
            if err == "glitch":
                await src.glitch()
                cov.hits["err:glitch"] += 1
                continue
            await src.send(b, skew=s, bad_parity=(err == "parity"), bad_stop=(err == "framing"))
            expected_rx.append((b, err == "parity", err == "framing"))
            e = None
            if err == "parity":
                e = f"parity_{cfg.parity}"
            elif err == "framing":
                e = "framing"
            cov.sample("rx", cfg, b, sname, e)
        await tx_task
        await ClockCycles(dut.clk, cfg.div * 2)

        got_tx = [f.data for f in tx_seen]
        got_rx = [(f.data, f.perr, f.ferr) for f in mon.frames]
        frames += len(tx_bytes) + len(expected_rx)
        if got_tx != tx_bytes or any(f.perr or f.ferr for f in tx_seen):
            errors += 1
            dut._log.error(f"TX div={cfg.div} {cfg.parity}: sent {tx_bytes} line {tx_seen}")
        if got_rx != expected_rx:
            errors += 1
            dut._log.error(f"RX div={cfg.div} {cfg.parity}: expected {expected_rx} got {got_rx}")
        for b in tx_bytes:
            cov.sample("tx", cfg, b)
        if cov.report()[0] == 100.0:
            break
    pct, miss, nbins = cov.report()
    dut._log.info(f"{rnd + 1} configurations, {frames} frames, {errors} errors, coverage {pct:.1f}% of {nbins} bins")
    assert errors == 0
    assert pct == 100.0, f"coverage holes: {miss}"
