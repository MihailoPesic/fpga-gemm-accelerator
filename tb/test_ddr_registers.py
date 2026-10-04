"""Register addresses and expected words are derived from the public contract."""
import json
import os
from pathlib import Path
import random

import cocotb
from common import tick


PARAMS = json.loads(os.environ['GEMM_PARAMETERS'])
CONFIG = {0x14: 'job_id', 0x18: 'm', 0x1c: 'n', 0x20: 'k', 0x24: 'a_base',
          0x28: 'bt_base', 0x2c: 'c_base', 0x30: 'a_stride', 0x34: 'bt_stride',
          0x38: 'c_stride', 0x3c: 'mode', 0x4c: 'watchdog'}
COUNTERS = {0x80: 'job_cycles', 0x88: 'compute_cycles', 0x90: 'read_beats',
            0x98: 'write_beats', 0xa0: 'write_valid_bytes', 0xa8: 'input_wait_cycles',
            0xb0: 'read_stall_cycles', 0xb8: 'write_stall_cycles'}
STATUS_FIELDS = ('job_ready', 'job_busy', 'job_done', 'job_error', 'ddr_ready', 'reset_required')
READ_ONLY = (0, 4, 8, 0x0c, 0x40, 0x44, 0x48, 0x50,
             *(address+word for address in COUNTERS for word in (0, 4)))
COVERAGE = {'parameters': PARAMS, 'seed': 20261113, 'requests': 0, 'responses': 0,
            'reads': 0, 'writes': 0, 'addresses': [], 'status_codes': [],
            'start_handshakes': 0, 'clear_pulses': 0, 'response_stall_cycles': 0,
            'start_stall_cycles': 0, 'resets': 0, 'busy_counter_rejections': 0,
            'fatal_counter_reads': 0, 'configuration_writes': 0}


def save_coverage():
    Path(os.environ['GEMM_COVERAGE']).write_text(json.dumps(COVERAGE, indent=2)+'\n')


class Harness:
    SIGNALS = ('req_valid', 'req_ready', 'req_write', 'req_addr', 'req_wdata', 'req_wstrb',
               'rsp_valid', 'rsp_ready', 'rsp_rdata', 'rsp_status',
               'job_start_valid', 'job_start_ready', 'job_start_rsp_valid',
               'job_start_rsp_ready', 'job_start_rsp_status', 'job_clear_status')

    def __init__(self, dut):
        self.dut = dut
        self.handles = {name: getattr(dut, name) for name in self.SIGNALS}
        self.rng = random.Random(COVERAGE['seed']+PARAMS['P']+PARAMS['T'])
        self.requests, self.responses, self.starts, self.job_responses, self.clears = [], [], 0, [], 0
        self.held = None
        self.inflight = False
        self.shadow = {address: 10000000 if name == 'watchdog' else 0 for address, name in CONFIG.items()}

    def put(self, **signals):
        for name, value in signals.items():
            getattr(self.dut, name).value = value

    def value(self, name):
        return int(getattr(self.dut, name).value)

    async def reset(self):
        self.put(clk=0, rst=1, req_valid=0, req_addr=0, req_write=0, req_wdata=0, req_wstrb=0,
                 rsp_ready=0, job_start_ready=0, job_start_rsp_valid=0, job_start_rsp_status=0,
                 job_clear_status_code=0, error_code=0, last_job_id=0)
        for name in (*STATUS_FIELDS, *COUNTERS.values()):
            self.put(**{name: 0})
        self.held = None
        self.inflight = False
        for _ in range(4):
            await tick(self.dut)
            assert not self.value('req_ready') and not self.value('rsp_valid')
            assert not self.value('job_start_valid') and not self.value('job_clear_status')
        self.put(rst=0, job_ready=1, ddr_ready=1)
        self.shadow = {address: 10000000 if name == 'watchdog' else 0 for address, name in CONFIG.items()}
        await self.step()
        assert self.value('req_ready')
        self.check_config()
        COVERAGE['resets'] += 1

    def check_config(self):
        for address, name in CONFIG.items():
            assert self.value('cfg_'+name) == self.shadow[address], (hex(address), name)

    async def step(self, **signals):
        self.put(**signals)
        s = await tick(self.dut, lambda: {name: int(handle.value) for name, handle in self.handles.items()})
        if self.held is not None:
            assert s['rsp_valid'] and (s['rsp_status'], s['rsp_rdata']) == self.held, 'Register response changed while stalled'
        if s['rsp_valid'] and not s['rsp_ready']:
            self.held = (s['rsp_status'], s['rsp_rdata'])
            COVERAGE['response_stall_cycles'] += 1
        else:
            self.held = None
        if s['req_valid'] and s['req_ready']:
            assert not self.inflight, 'Accepted a second outstanding register request'
            self.inflight = True
            request = (s['req_addr'], s['req_write'], s['req_wdata'], s['req_wstrb'])
            self.requests.append(request)
            COVERAGE['requests'] += 1
            COVERAGE['writes' if s['req_write'] else 'reads'] += 1
            COVERAGE['addresses'] = sorted(set(COVERAGE['addresses']+[s['req_addr']]))
        if s['rsp_valid'] and s['rsp_ready']:
            assert self.inflight, 'Response without a register request'
            self.inflight = False
            self.responses.append((s['rsp_status'], s['rsp_rdata']))
            COVERAGE['responses'] += 1
            COVERAGE['status_codes'] = sorted(set(COVERAGE['status_codes']+[s['rsp_status']]))
        if s['job_start_valid']:
            if s['job_start_ready']:
                self.starts += 1
                COVERAGE['start_handshakes'] += 1
            else:
                COVERAGE['start_stall_cycles'] += 1
        if s['job_start_rsp_valid'] and s['job_start_rsp_ready']:
            self.job_responses.append(s['job_start_rsp_status'])
        if s['job_clear_status']:
            self.clears += 1
            COVERAGE['clear_pulses'] += 1
        return s

    async def idle(self, cycles):
        for _ in range(cycles):
            await self.step()

    async def until(self, condition, limit=100):
        for _ in range(limit):
            s = await self.step()
            if condition(s):
                return s
        raise AssertionError(f'Register interface timeout: {condition}')

    async def issue(self, address, write=False, data=0, strobe=15):
        self.put(req_valid=1, req_addr=address, req_write=int(write), req_wdata=data,
                 req_wstrb=strobe, rsp_ready=0)
        await self.until(lambda s: s['req_ready'])
        # The client may immediately change every payload field after acceptance.
        self.put(req_valid=0, req_addr=0xffff, req_write=int(not write),
                 req_wdata=data ^ 0xffffffff, req_wstrb=strobe ^ 15)

    async def reply(self, expected, hold=0):
        await self.until(lambda s: s['rsp_valid'])
        assert (self.value('rsp_status'), self.value('rsp_rdata')) == expected, (
            self.value('rsp_status'), self.value('rsp_rdata'), expected)
        await self.idle(hold)
        self.put(rsp_ready=1)
        await self.step()
        self.put(rsp_ready=0)
        assert self.responses[-1] == expected

    async def transaction(self, address, write=False, data=0, strobe=15, status=0, value=0, hold=0):
        before = (self.starts, self.clears)
        await self.issue(address, write, data, strobe)
        await self.reply((status, value), hold)
        assert (self.starts, self.clears) == before, 'Ordinary register access triggered a command'
        if write and status == 0 and address in CONFIG:
            self.shadow[address] = data
            COVERAGE['configuration_writes'] += 1
        self.check_config()


@cocotb.test()
async def register_map_and_access_rules(dut):
    h = Harness(dut)
    await h.reset()
    identity = {0: PARAMS['ID'], 4: PARAMS['VERSION'], 8: 0x01000000+(PARAMS['T'] << 8)+PARAMS['P'],
                0x10: 0, 0x48: PARAMS['CORE_HZ'], 0x50: PARAMS['BUILD_ID']}
    for address, value in identity.items():
        await h.transaction(address, value=value, hold=3)
    for address, value in h.shadow.items():
        await h.transaction(address, value=value)
    for bits in range(64):
        h.put(**{name: (bits >> index) & 1 for index, name in enumerate(STATUS_FIELDS)})
        await h.transaction(0x0c, value=bits)
    h.put(**{name: int(name in ('job_ready', 'ddr_ready')) for name in STATUS_FIELDS})
    h.put(error_code=0x8017, last_job_id=0xfedcba98)
    await h.transaction(0x40, value=0x8017)
    await h.transaction(0x44, value=0xfedcba98)

    for address in CONFIG:
        for value in (1, 0x80000000, 0xffffffff, h.rng.getrandbits(32)):
            await h.transaction(address, write=True, data=value, hold=2)
            await h.transaction(address, value=value)
    # Dimensions, address fields and MODE retain raw words. The job controller
    # validates their cross-field meaning only when START is accepted.
    for address in CONFIG:
        if address != 0x4c:
            await h.transaction(address, write=True, data=0)
    await h.transaction(0x4c, write=True, data=0, status=1)
    for address in READ_ONLY:
        await h.transaction(address, write=True, data=0xffffffff, status=1)
    for strobe in range(15):
        await h.transaction(0x14, write=True, data=0x55aa55aa, strobe=strobe, status=1)
        await h.transaction(0x10, write=True, data=1, strobe=strobe, status=1)
    for strobe in range(16):
        await h.transaction(0, strobe=strobe, value=PARAMS['ID'])
    for address in (1, 2, 3, 0x15, 0x4f, 0x81, 0xffff, 0x54, 0x60, 0x7c, 0xc0, 0xfffc):
        for write in (False, True):
            await h.transaction(address, write=write, data=1, status=2)
    h.put(job_busy=1)
    for address in CONFIG:
        await h.transaction(address, write=True, data=1, status=4)
        await h.transaction(address, value=h.shadow[address])
    # Frozen error state does not block idle descriptor edits. It is the job
    # controller that refuses START/CLEAR until a coordinated platform reset.
    h.put(job_busy=0, reset_required=1)
    await h.transaction(0x14, write=True, data=0xdeadbeef)
    save_coverage()


@cocotb.test()
async def counter_words_and_response_stability(dut):
    h = Harness(dut)
    await h.reset()
    for index, name in enumerate(COUNTERS.values()):
        h.put(**{name: ((0x80000000+index) << 32) | (0xfedcba98-index*0x01020304)})
    await h.idle(1)
    for address, name in COUNTERS.items():
        value = h.value(name)
        await h.transaction(address, value=value & 0xffffffff, hold=5)
        await h.transaction(address+4, value=value >> 32, hold=5)
    h.put(job_busy=1)
    for address in COUNTERS:
        for offset in (0, 4):
            await h.transaction(address+offset, status=4)
            COVERAGE['busy_counter_rejections'] += 1
    h.put(reset_required=1)
    for address, name in COUNTERS.items():
        value = h.value(name)
        await h.transaction(address, value=value & 0xffffffff)
        await h.transaction(address+4, value=value >> 32)
        COVERAGE['fatal_counter_reads'] += 2

    h.put(job_busy=0, reset_required=0, job_cycles=0x1122334455667788)
    await h.issue(0x80)
    await h.until(lambda s: s['rsp_valid'])
    captured = (h.value('rsp_status'), h.value('rsp_rdata'))
    assert captured == (0, 0x55667788)
    count_before = len(h.requests)
    h.put(job_cycles=0xffeeddccbbaa9988, job_busy=1, req_valid=1,
          req_addr=0, req_write=0, req_wdata=0, req_wstrb=15)
    await h.idle(30)
    assert len(h.requests) == count_before and h.starts == 0
    assert (h.value('rsp_status'), h.value('rsp_rdata')) == captured
    await h.reply(captured)
    # Keep the offered second request stable until it is actually accepted.
    await h.until(lambda s: s['req_valid'] and s['req_ready'])
    h.put(req_valid=0)
    await h.reply((0, PARAMS['ID']))
    save_coverage()


@cocotb.test()
async def deferred_start_and_clear_commands(dut):
    h = Harness(dut)
    await h.reset()
    for address in CONFIG:
        await h.transaction(address, write=True, data=address+1)
    for status in (0, 3, 5, 4):
        starts_before = h.starts
        h.put(job_start_ready=0, job_start_rsp_valid=0)
        await h.issue(0x10, write=True, data=1)
        await h.until(lambda s: s['job_start_valid'])
        await h.idle(12)
        assert not h.value('rsp_valid') and not h.value('req_ready')
        assert h.starts == starts_before and h.value('job_start_valid')
        h.check_config()
        h.put(job_start_ready=1)
        await h.until(lambda s: s['job_start_valid'] and s['job_start_ready'])
        h.put(job_start_ready=0)
        await h.idle(15)
        assert not h.value('rsp_valid') and h.starts == starts_before+1
        h.put(job_start_rsp_valid=1, job_start_rsp_status=status)
        await h.until(lambda s: s['job_start_rsp_valid'] and s['job_start_rsp_ready'])
        h.put(job_start_rsp_valid=0, job_start_rsp_status=status ^ 0xffff)
        await h.reply((status, 0), hold=14)
        await h.idle(3)
        assert h.starts == starts_before+1 and h.job_responses[-1] == status
    for status in (0, 4, 5):
        before = h.clears
        h.put(job_clear_status_code=status)
        await h.issue(0x10, write=True, data=2)
        await h.until(lambda s: s['job_clear_status'])
        await h.reply((status, 0), hold=15)
        assert h.clears == before+1
    for value in (0, 3, 4, 0xffffffff):
        await h.transaction(0x10, write=True, data=value, status=1)
    # A command received while busy cannot become queued work merely because
    # the running job finishes between local acceptance and decode. Conversely,
    # becoming busy after request acceptance must also prevent the mutation.
    for busy_at_acceptance in (0, 1):
        for address, data in ((0x10, 1), (0x10, 2), (0x14, 0x89abcdef)):
            before = (h.starts, h.clears)
            h.put(job_busy=busy_at_acceptance)
            await h.issue(address, write=True, data=data)
            h.put(job_busy=1-busy_at_acceptance)
            await h.reply((4, 0), hold=3)
            assert (h.starts, h.clears) == before
            h.check_config()
    h.put(job_busy=0)
    save_coverage()


@cocotb.test()
async def reset_and_back_to_back_requests(dut):
    h = Harness(dut)
    await h.reset()
    for index in range(20):
        address = tuple(CONFIG)[index % len(CONFIG)]
        value = index+1
        await h.transaction(address, write=True, data=value)
        await h.transaction(address, value=value)
    # A coordinated reset cancels a held register response and resets config.
    await h.issue(0x14, write=True, data=0x12345678)
    await h.until(lambda s: s['rsp_valid'])
    await h.reset()
    await h.transaction(0x14, value=0)
    # Reset an unaccepted local job START; no hidden later pulse may survive it.
    await h.issue(0x10, write=True, data=1)
    await h.until(lambda s: s['job_start_valid'])
    starts_before = h.starts
    await h.reset()
    h.put(job_start_ready=1)
    await h.idle(10)
    assert h.starts == starts_before and not h.value('rsp_valid')
    # Reset while awaiting job validation; an obsolete job response is ignored.
    await h.issue(0x10, write=True, data=1)
    await h.until(lambda s: s['job_start_valid'] and s['job_start_ready'])
    await h.reset()
    h.put(job_start_rsp_valid=1, job_start_rsp_status=0)
    await h.idle(10)
    assert not h.value('job_start_rsp_ready') and not h.value('rsp_valid')
    h.put(job_start_rsp_valid=0)
    await h.transaction(0x4c, value=10000000)
    save_coverage()
