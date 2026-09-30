"""Test utilities; no RTL algorithm or schedule is used by the oracle."""
from cocotb.triggers import Timer


async def tick(dut, before=None):
    """Drive on the low phase, observe settled inputs, then advance one edge."""
    await Timer(5, unit="ns")
    observed = before() if before else None
    dut.clk.value = 1
    await Timer(5, unit="ns")
    dut.clk.value = 0
    return observed


def signed(value, width=32):
    return value - (1 << width) if value & (1 << (width - 1)) else value


def pack(values, width=8):
    return sum((value & ((1 << width) - 1)) << (width * lane)
               for lane, value in enumerate(values))


def matmul(a, bt, rows, cols):
    # Python arbitrary-precision integers: this cannot silently multiply INT8.
    return [[sum(int(x) * int(y) for x, y in zip(a[r], bt[c], strict=True))
             for c in range(cols)] for r in range(rows)]
