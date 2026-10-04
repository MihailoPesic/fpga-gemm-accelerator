"""Validated-job commands, DDR bytes, ownership and independently sampled counters."""
import json
import os
from pathlib import Path
import random

import cocotb
from cocotb.triggers import Timer
from common import tick
import test_tile_scheduler as scheduler_test


COUNTERS = ('job_cycles', 'compute_cycles', 'read_beats', 'write_beats',
            'write_valid_bytes', 'input_wait_cycles', 'read_stall_cycles', 'write_stall_cycles')
COVERAGE = scheduler_test.COVERAGE
COVERAGE.update(shell_seed=20261005, seeded_jobs=0, invalid_descriptors=0,
                command_statuses={}, shell_faults={}, counter_snapshots=[],
                requested_random_jobs=int(os.environ.get('GEMM_RANDOM_JOBS', '100')))


def save_coverage(scenario):
    COVERAGE['scenarios'].append(scenario)
    Path(os.environ['GEMM_COVERAGE']).write_text(json.dumps(COVERAGE, indent=2)+'\n')


class Harness(scheduler_test.Harness):
    CHANNELS = {**scheduler_test.Harness.CHANNELS,
                'start_rsp': ('start_rsp_valid', 'start_rsp_ready', ('start_rsp_status',))}
    INPUTS = ('start_valid', 'start_rsp_ready', 'clear_status', 'host_busy',
              'inject_response_error', 'inject_response_strb', 'inject_rd_tag',
              'inject_rd_index', 'inject_rd_last', 'inject_wr_done', 'inject_spurious_b',
              'inject_mem_fatal', 'inject_mem_code', 'inject_rresp', 'inject_bresp',
              'inject_scheduler_fatal', 'inject_scheduler_code', 'inject_compute_error',
              'suppress_progress', 'hold_local_idle', 'hold_axi_quiescent')

    def __init__(self, dut, random_stalls=True):
        super().__init__(dut, random_stalls)
        self.telemetry = dut.job.scheduler
        self.accepted_edges = []
        self.fault_edges = []
        self.reset_oracle()

    def reset_oracle(self):
        self.active_epoch = False
        self.accepted_cycle = self.last_b_cycle = None
        self.current_desc = self.pending_desc = None
        self.run = dict.fromkeys(COUNTERS[1:], 0)
        self.core_inflight = self.core_seen_busy = False
        self.progress_idle = 0
        self.was_fatal = self.was_done = False
        self.frozen = None

    def counters(self):
        return tuple(self.value(name) for name in COUNTERS)

    @staticmethod
    def descriptor(m, n, k, mode, job_id=1, watchdog=100000):
        return dict(scheduler_test.Harness.descriptor(m, n, k, mode),
                    job_id=job_id, watchdog=watchdog)

    async def reset(self, calibrate=True):
        self.put(clk=0, rst=1, ddr_ready=0)
        for name in self.INPUTS:
            self.put(**{name: 0})
        self.configure(self.descriptor(1, 1, 1, 0))
        for name in self.GATES:
            self.put(**{name: 1})
        self.fixed.clear()
        for channel in self.channels:
            channel.pause = False
        for _ in range(5):
            await tick(self.dut)
        self.put(rst=0)
        self.cycle = 0
        self.logs = {name: [] for name in self.CHANNELS}
        self.held.clear()
        self.external_reads.clear()
        self.local_reads.clear()
        self.frozen_fault = None
        self.request_held.clear()
        self.model = None
        self.load_owner = self.store_owner = self.core_owner = None
        self.input_owners.clear()
        self.output_owners.clear()
        self.operation_logs = {key: [] for key in self.operation_logs}
        self.accepted_edges.clear()
        self.fault_edges.clear()
        self.reset_oracle()
        await self.idle(2)
        assert not self.value('ready') and not self.value('error')
        if calibrate:
            self.put(ddr_ready=1)
            await self.idle(2)
            assert self.value('ready')

    def expected_input_wait(self):
        if not self.active_epoch or self.model is None or self.core_inflight:
            return False
        cursor = self.model['compute_cursor']
        if cursor == len(self.model['tiles']) or len(self.output_owners) == 2:
            return False
        if self.current_desc['mode'] == 0 and cursor != self.model['retire_cursor']:
            return False
        prepared = any(owner['tag'] == cursor and owner['loaded'] == {0, 1}
                       for owner in self.input_owners.values())
        return not prepared

    async def step(self, **values):
        self.put(**values)
        await Timer(1, unit='ps')
        pre = {name: self.value(name) for name in
               ('start_valid', 'start_ready', 'clear_status_code', 'compute_cycles_in',
                'compute_done', 'compute_busy', 'reset_required', 'ddr_ready', 'host_busy',
                'suppress_progress', 'inject_scheduler_fatal', 'inject_bresp')}
        pre_counters = self.counters()
        was_active = self.active_epoch
        inflight = self.core_inflight
        complete = inflight and self.core_seen_busy and pre['compute_done'] and not pre['compute_busy']
        input_wait = self.expected_input_wait()
        if was_active:
            assert int(self.telemetry.compute_inflight.value) == int(inflight)
            assert int(self.telemetry.compute_complete.value) == int(complete)
            assert int(self.telemetry.input_waiting.value) == int(input_wait), 'Input-wait predicate disagrees with public ownership'
        completed_before = self.run['compute_cycles']
        idle_before = self.progress_idle
        s = await super().step()
        s.update(pre)
        core_fire = not s['compute_busy'] and self.value('compute_busy')
        r_fire = s['m_axi_rvalid'] and s['m_axi_rready'] and not pre['host_busy']
        w_fire = s['m_axi_wvalid'] and s['m_axi_wready'] and not pre['host_busy']
        b_fire = s['m_axi_bvalid'] and s['m_axi_bready'] and not pre['host_busy']
        effective_bresp = pre['inject_bresp'] or s['m_axi_bresp']
        memory_progress = any(s[valid] and s[ready] for valid, ready in
                              (('m_axi_arvalid', 'm_axi_arready'), ('m_axi_rvalid', 'm_axi_rready'),
                               ('m_axi_awvalid', 'm_axi_awready'), ('m_axi_wvalid', 'm_axi_wready'),
                               ('m_axi_bvalid', 'm_axi_bready'), ('wr_data_valid', 'wr_data_ready'),
                               ('rd_data_valid', 'rd_data_ready')))
        progress = ((memory_progress or s['compute_busy']) and not pre['suppress_progress']) or core_fire or complete
        progress |= r_fire or w_fire or b_fire
        progress |= any(s[direction+'_req_valid'] and s[direction+'_req_ready'] or
                        s[direction+'_done_valid'] and s[direction+'_done_ready']
                        for direction in ('load', 'store'))
        if was_active:
            self.run['compute_cycles'] += pre['compute_cycles_in'] if complete else 0
            self.run['read_beats'] += int(r_fire)
            self.run['write_beats'] += int(w_fire)
            self.run['write_valid_bytes'] += s['m_axi_wstrb'].bit_count() if w_fire else 0
            self.run['input_wait_cycles'] += int(input_wait)
            self.run['read_stall_cycles'] += int(s['m_axi_rvalid'] and not s['m_axi_rready'] and not pre['host_busy'])
            self.run['write_stall_cycles'] += int(s['m_axi_wvalid'] and not s['m_axi_wready'] and not pre['host_busy'])
            if b_fire and effective_bresp == 0:
                self.last_b_cycle = self.cycle
            self.progress_idle = 0 if progress else self.progress_idle+1
        if inflight and s['compute_busy']:
            self.core_seen_busy = True
        if complete:
            self.core_inflight = self.core_seen_busy = False
        if core_fire:
            self.core_inflight, self.core_seen_busy = True, False
        if self.value('job_accepted'):
            assert not was_active and self.pending_desc is not None
            self.accepted_cycle = self.cycle
            self.last_b_cycle = None
            self.current_desc = self.pending_desc.copy()
            self.run = dict.fromkeys(COUNTERS[1:], 0)
            self.progress_idle = 0
            self.active_epoch = True
            self.frozen = None
            self.accepted_edges.append((self.cycle, self.value('last_job_id')))
            assert self.value('last_job_id') == self.current_desc['job_id']
            assert self.value('busy') and not self.value('done') and self.counters() == (0,)*8
        post_fatal = bool(self.value('reset_required'))
        if post_fatal and not self.was_fatal:
            code = self.value('error_code')
            if was_active:
                expected = [self.cycle-self.accepted_cycle,
                            completed_before+(pre['compute_cycles_in'] if inflight else 0)]
                expected += [self.run[name] for name in COUNTERS[2:]]
                assert self.counters() == tuple(expected), f'First-fault counter snapshot: {self.counters()} != {expected}'
                if code == 9 and not pre['inject_scheduler_fatal']:
                    assert not progress and idle_before >= self.current_desc['watchdog']-1
            else:
                assert self.counters() == pre_counters, 'Idle fault changed prior job counters'
            self.frozen = self.counters()
            self.fault_edges.append((self.cycle, code, self.frozen))
            COVERAGE['shell_faults'][str(code)] = COVERAGE['shell_faults'].get(str(code), 0)+1
            self.active_epoch = False
        post_done = bool(self.value('done'))
        if post_done and not self.was_done:
            assert was_active and not post_fatal and self.last_b_cycle is not None
            expected = (self.last_b_cycle-self.accepted_cycle,
                        *(self.run[name] for name in COUNTERS[1:]))
            assert self.counters() == expected, f'Success counter snapshot: {self.counters()} != {expected}'
            self.frozen = expected
            self.active_epoch = False
            COVERAGE['counter_snapshots'].append(dict(job_id=self.value('last_job_id'),
                                                       counters=dict(zip(COUNTERS, expected))))
        if self.frozen is not None:
            assert self.counters() == self.frozen, 'Public counters changed after a frozen snapshot'
        self.was_fatal, self.was_done = post_fatal, post_done
        return s

    def prepare_model(self, d):
        self.model = dict(desc=d.copy(), load_cursor=0, compute_cursor=0, retire_cursor=0,
                          tiles=[dict(i=i, j=j, r=min(self.t, d['m']-i), c=min(self.t, d['n']-j))
                                 for i in range(0, d['m'], self.t) for j in range(0, d['n'], self.t)])
        self.load_owner = self.store_owner = self.core_owner = None
        self.input_owners.clear()
        self.output_owners.clear()
        self.core_inflight = self.core_seen_busy = False

    async def release_response(self):
        assert self.value('start_rsp_valid')
        s = await self.step(start_rsp_ready=1)
        assert s['start_rsp_valid'] and s['start_rsp_ready']
        self.put(start_rsp_ready=0)

    async def request_start(self, d, expected=0, *, consume=True, model=False):
        assert self.value('start_ready')
        if model:
            self.prepare_model(d)
        self.pending_desc = d.copy()
        before = len(self.accepted_edges)
        self.configure(d)
        await self.until_start()
        # Validation must use its snapshot, not configuration changing later.
        self.configure({key: 0 for key in d})
        await self.until(lambda s: self.value('start_rsp_valid'))
        assert self.value('start_rsp_status') == expected
        assert len(self.accepted_edges)-before == int(expected == 0)
        COVERAGE['command_statuses'][str(expected)] = COVERAGE['command_statuses'].get(str(expected), 0)+1
        if consume:
            await self.release_response()
        return expected

    async def until_start(self):
        self.put(start_valid=1, start_rsp_ready=0)
        await self.until(lambda s: s['start_valid'] and s['start_ready'])
        self.put(start_valid=0)

    async def launch_job(self, d, *, consume=True):
        assert self.value('ready')
        await self.request_start(d, consume=consume, model=True)
        self.operation_logs['job'].append(self.accepted_cycle)

    async def check_job(self, d, images, mark):
        await super().check_job(d, images, mark)
        expected_compute = ((d['m']+self.p-1)//self.p)*((d['n']+self.p-1)//self.p)*(d['k']+3*self.p-1)
        expected_reads = ((d['k']+7)//8)*(d['m']*((d['n']+self.t-1)//self.t)+d['n']*((d['m']+self.t-1)//self.t))
        assert self.value('compute_cycles') == expected_compute
        assert self.value('read_beats') == expected_reads
        assert self.value('write_beats') == d['m']*((d['n']+1)//2)
        assert self.value('write_valid_bytes') == 4*d['m']*d['n']
        assert self.value('last_job_id') == d['job_id']

    async def clear(self, expected=0):
        self.put(clear_status=1)
        await Timer(1, unit='ps')
        assert self.value('clear_status_code') == expected
        await self.step()
        self.put(clear_status=0)
        if expected == 0:
            assert not self.value('done') and not self.value('error')


@cocotb.test()
async def matched_jobs_both_modes(dut):
    h = Harness(dut)
    await h.reset()
    shapes = [(1, 1, 1), (h.p+1, h.p-1, 9), (h.t+1, h.t+3, 33),
              (3, 5, 255), (h.p, h.p, 256)]
    for index, shape in enumerate(shapes):
        images = h.images(h.descriptor(*shape, 0))
        for mode in (0, 1):
            d = h.descriptor(*shape, mode, job_id=100+2*index+mode)
            for address, initial in h.initial_images:
                h.ram.write(address, initial)
            mark = h.mark()
            await h.launch_job(d)
            if index == 2 and mode == 1:
                await h.until(lambda s: h.model['load_cursor'] == 2 and s['load_done_valid'] and s['load_done_ready'])
                h.fixed['allow_load_done'] = 0
                await h.until(lambda s: h.model['retire_cursor'] == 1)
                assert h.model['compute_cursor'] == 1 and not h.output_owners
                h.fixed['allow_load_done'] = 1
                await h.until(lambda s: h.model['compute_cursor'] == 2)
                launch = h.operation_logs['core'][-1][1]
                assert launch[3] != launch[4]
            await h.check_job(d, images, mark)
            dut._log.info('Checked M=%d N=%d K=%d MODE=%d', *shape, mode)
    save_coverage('validated_both_modes_same_inputs_tails_counter_identity')


@cocotb.test()
async def validation_and_start_contract(dut):
    h = Harness(dut, random_stalls=False)
    await h.reset()
    d = h.descriptor(3, 5, 9, 1, job_id=0xabcdef01)
    images, mark = h.images(d), h.mark()
    h.fixed['allow_store_done'] = 0
    await h.launch_job(d, consume=False)
    await h.idle(20)
    assert h.value('start_rsp_valid') and h.value('start_rsp_status') == 0
    assert not h.value('start_ready') and h.logs['ar'], 'Held START reply blocked accepted work'
    accepted = len(h.accepted_edges)
    h.configure(h.descriptor(1, 1, 1, 0, job_id=99))
    await h.idle(3)
    h.put(start_valid=1)
    await h.idle(3)
    h.put(start_valid=0)
    assert len(h.accepted_edges) == accepted and h.value('start_rsp_status') == 0
    await h.release_response()
    await h.request_start(h.descriptor(1, 1, 1, 0, job_id=99), 4)
    await h.clear(4)
    h.fixed['allow_store_done'] = 1
    await h.check_job(d, images, mark)
    previous = h.counters(), h.value('last_job_id')
    base = h.descriptor(3, 17, 9, 1, job_id=2)
    invalid = [dict(base, **change) for change in (
        {'m': 0}, {'m': 1025}, {'m': 0x40000001}, {'n': 0}, {'n': 1025},
        {'n': 0xffffffff}, {'k': 0}, {'k': 257}, {'mode': 2}, {'mode': 0x80000000},
        {'watchdog': 0}, {'a_base': 1}, {'bt_base': 1}, {'c_base': 1},
        {'a_base': 1 << 27}, {'bt_base': 1 << 27}, {'c_base': 1 << 27},
        {'a_stride': 0}, {'bt_stride': 0}, {'c_stride': 0},
        {'a_stride': 1}, {'bt_stride': 65}, {'c_stride': 64},
        {'a_stride': 0xffffffc0}, {'bt_stride': 0xffffffc0}, {'c_stride': 0xffffffc0},
        {'bt_base': base['a_base']}, {'c_base': base['a_base']},
        {'c_base': base['bt_base']}, {'a_base': (1 << 27)-64})]
    for bad in invalid:
        traffic = {key: len(h.logs[key]) for key in ('ar', 'aw', 'w', 'load', 'read')}
        await h.request_start(bad, 3)
        assert traffic == {key: len(h.logs[key]) for key in traffic}
        assert (h.counters(), h.value('last_job_id')) == previous and h.value('done')
        assert h.value('error_code') == 3 and not h.value('reset_required')
        COVERAGE['invalid_descriptors'] += 1
    h.put(host_busy=1)
    await h.request_start(base, 5)
    assert (h.counters(), h.value('last_job_id')) == previous
    h.put(host_busy=0)
    await h.clear()
    # Maximum legal dimensions/allocations are an admission test, not a result test.
    maximum = h.descriptor(1024, 1024, 256, 1, job_id=0xffffffff)
    maximum_mark = h.mark()
    await h.launch_job(maximum, consume=False)
    h.put(inject_scheduler_fatal=1, inject_scheduler_code=9)
    await h.fatal_drained(9)
    assert h.mark() == maximum_mark, 'Maximum admission issued traffic before the injected stop'
    await h.release_response()
    save_coverage('invalid_no_dma_snapshot_reply_busy_host_max_admission')


@cocotb.test()
async def final_b_and_counter_snapshots(dut):
    h = Harness(dut, random_stalls=False)
    await h.reset()
    d = h.descriptor(1, 3, 9, 1, job_id=301)
    images, mark = h.images(d), h.mark()
    h.ram.write_if.b_channel.pause = True
    await h.launch_job(d)
    await h.until(lambda s: s['m_axi_wvalid'] and s['m_axi_wready'] and s['m_axi_wlast'])
    await h.idle(17)
    assert h.value('busy') and not h.value('done') and not h.value('ready') and not h.logs['b']
    h.put(hold_local_idle=1)
    h.ram.write_if.b_channel.pause = False
    await h.until(lambda s: h.model['retire_cursor'] == 1)
    actual_b = h.last_b_cycle
    await h.idle(13)
    assert h.value('busy') and not h.value('done') and h.last_b_cycle == actual_b
    h.put(hold_local_idle=0)
    await h.check_job(d, images, mark)
    assert h.value('job_cycles') == actual_b-h.accepted_cycle
    # INPUT_WAIT excludes a core waiting for space, even if later inputs are absent.
    d = h.descriptor(2*h.t+1, h.t+1, 9, 1, job_id=302)
    images, mark = h.images(d), h.mark()
    h.fixed['allow_store_done'] = 0
    await h.launch_job(d)
    await h.until(lambda s: h.model['load_cursor'] == 4 and s['load_done_valid'] and s['load_done_ready'])
    h.fixed['allow_load_done'] = 0
    await h.until(lambda s: len(h.output_owners) == 2 and h.core_owner is None)
    assert not any(owner['tag'] == h.model['compute_cursor'] and owner['loaded'] == {0, 1}
                   for owner in h.input_owners.values()), 'Resource stall did not also withhold the next input'
    before = h.run['input_wait_cycles']
    await h.idle(15)
    assert len(h.output_owners) == 2 and h.run['input_wait_cycles'] == before
    h.fixed.update(allow_load_done=1, allow_store_done=1)
    await h.check_job(d, images, mark)
    save_coverage('last_raw_b_epoch_delayed_done_output_resource_wait')


@cocotb.test()
async def fault_counters_and_drain(dut):
    h = Harness(dut, random_stalls=False)
    for source, code in (('read', 7), ('write', 7), ('compute', 8), ('external', 9)):
        await h.reset()
        d = h.descriptor(h.t+1, h.t+1, 33, 1, job_id=400+code)
        h.images(d)
        if source == 'write':
            h.ram.write_if.b_channel.pause = True
        await h.launch_job(d)
        if source == 'read':
            h.fixed['allow_load'] = 0
            await h.until(lambda s: s['load_valid'] and not s['load_ready'])
            h.put(inject_rresp=2)
            h.fixed['allow_load'] = 1
        elif source == 'write':
            await h.until(lambda s: s['m_axi_wvalid'] and s['m_axi_wready'] and s['m_axi_wlast'])
            h.put(inject_bresp=2)
            h.ram.write_if.b_channel.pause = False
        else:
            await h.until(lambda s: s['compute_busy'] and s['load_busy'])
            h.put(**({'inject_compute_error': 1} if source == 'compute' else
                     {'inject_scheduler_fatal': 1, 'inject_scheduler_code': 9}))
        await h.fatal_drained(code)
        frozen = h.counters()
        h.put(inject_compute_error=0, inject_scheduler_fatal=1, inject_scheduler_code=10)
        await h.idle(8)
        assert h.value('error_code') == code and h.counters() == frozen
        await h.clear(5)
        await h.request_start(d, 5)
        assert h.counters() == frozen
    # Successful final local terminal and a fault coincide; fatal wins.
    await h.reset()
    d = h.descriptor(1, 1, 1, 1, job_id=499)
    h.images(d)
    h.fixed['allow_store_done'] = 0
    await h.launch_job(d)
    await h.until(lambda s: s['store_done_valid'])
    h.fixed['allow_store_done'] = 1
    h.put(inject_scheduler_fatal=1, inject_scheduler_code=10)
    await h.fatal_drained(10)
    save_coverage('fault_edge_exact_counters_retained_obligations_fatal_wins')


@cocotb.test()
async def calibration_watchdog_and_clear(dut):
    h = Harness(dut, random_stalls=False)
    await h.reset(calibrate=False)
    d = h.descriptor(1, 1, 1, 0, job_id=501)
    await h.request_start(d, 5)
    assert not h.value('reset_required') and not h.logs['ar']
    h.put(ddr_ready=1)
    await h.idle(2)
    h.configure(d)
    h.pending_desc = d.copy()
    await h.until_start()
    h.put(ddr_ready=0)
    await h.until(lambda s: h.value('start_rsp_valid'))
    assert h.value('start_rsp_status') == 5 and h.value('reset_required') and h.value('error_code') == 10
    assert not h.logs['ar'] and not h.logs['aw']
    await h.release_response()
    if h.value('busy'):
        await h.clear(4)
    await h.fatal_drained(10)
    await h.clear(5)
    for limit in (1, 4):
        await h.reset()
        d = h.descriptor(1, 1, 1, 1, job_id=510+limit, watchdog=limit)
        h.images(d)
        h.ram.read_if.ar_channel.pause = True
        await h.launch_job(d)
        await h.until(lambda s: h.value('reset_required'))
        assert h.value('error_code') == 9 and not h.value('done')
        frozen = h.counters()
        await h.idle(6)
        assert h.counters() == frozen
        h.ram.read_if.ar_channel.pause = False
        await h.fatal_drained(9)
    await h.reset()
    d = h.descriptor(1, 3, 9, 1, job_id=520)
    images, mark = h.images(d), h.mark()
    await h.launch_job(d)
    await h.check_job(d, images, mark)
    previous = h.counters()
    h.put(ddr_ready=0)
    await h.step()
    assert h.value('error_code') == 10 and not h.value('done') and h.counters() == previous
    await h.fatal_drained(10)
    h.put(ddr_ready=1)
    await h.idle(3)
    assert not h.value('ready')
    save_coverage('startup_validation_calibration_idle_loss_watchdog_thresholds_clear')


@cocotb.test()
async def seeded_mixed_jobs(dut):
    h = Harness(dut)
    await h.reset()
    rng = random.Random(COVERAGE['shell_seed']+h.p+h.t)
    reductions = (1, 2, 7, 8, 9, 16, 31, 32, 33, 63, 255, 256)
    for index in range(COVERAGE['requested_random_jobs']):
        # Frequent small/tail jobs plus regular larger jobs across several
        # macrotiles keep the seed set useful without dominating CI runtime.
        dimension_limit = 97 if index % 10 == 9 else 17
        m, n, k = rng.randint(1, dimension_limit), rng.randint(1, dimension_limit), rng.choice(reductions)
        d = h.descriptor(m, n, k, index % 2, job_id=1000+index)
        images, mark = h.images(d), h.mark()
        await h.launch_job(d)
        await h.check_job(d, images, mark)
        COVERAGE['seeded_jobs'] += 1
        if (index+1) % 10 == 0:
            dut._log.info('Checked %d/%d seeded jobs', index+1, COVERAGE['requested_random_jobs'])
    save_coverage('seeded_mixed_shapes_modes_k_boundaries_full_comparison')
