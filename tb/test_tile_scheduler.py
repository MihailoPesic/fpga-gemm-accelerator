"""Tile-order and lifetime scoreboard at public scheduler handshakes."""
import json
import hashlib
import os
from pathlib import Path

import cocotb
from common import tick
import test_tile_dma_duplex as dma_test


COVERAGE = dma_test.COVERAGE
COVERAGE.update(macrotiles=0, modes=[], scheduler_load_overlap_edges=0,
                scheduler_store_overlap_edges=0, scheduler_three_way_edges=0,
                independent_buffer_ids=0, held_completion_checks=0, fatal_cases=[])
COVERAGE['case_results'] = []
COVERAGE['maximum_tile_counts'] = []


def save_coverage(scenario):
    COVERAGE['scenarios'].append(scenario)
    Path(os.environ['GEMM_COVERAGE']).write_text(json.dumps(COVERAGE, indent=2)+'\n')


class DutView:
    """Reuse the independent AXI/bank monitor through the nested test fixture."""
    def __init__(self, dut):
        self.root = dut

    def __getattr__(self, name):
        try:
            handle = getattr(self.root, name)
        except AttributeError:
            handle = getattr(self.root.datapath, name)
        # Cache the resolved handle, never its changing signal value.
        setattr(self, name, handle)
        return handle

    def __dir__(self):
        return sorted(set(dir(self.root)) | set(dir(self.root.datapath)))


class Harness(dma_test.Harness):
    GATES = (*dma_test.Harness.GATES, 'allow_load_done', 'allow_store_done', 'allow_compute')
    INPUTS = ('job_valid', 'inject_response_error', 'inject_response_strb', 'inject_rd_tag',
              'inject_rd_index', 'inject_rd_last', 'inject_wr_done', 'inject_spurious_b',
              'inject_mem_fatal', 'inject_mem_code', 'inject_rresp', 'inject_bresp',
              'inject_scheduler_fatal', 'inject_scheduler_code', 'inject_compute_error',
              'hold_local_idle', 'hold_axi_quiescent')

    def __init__(self, dut, random_stalls=True):
        super().__init__(DutView(dut), random_stalls)
        self.raw = dut
        self.operation_logs = {key: [] for key in ('load_req', 'store_req', 'core', 'core_done', 'retire', 'job')}
        self.request_held = {}
        self.model = None
        self.load_owner = self.store_owner = self.core_owner = None
        self.input_owners = {}
        self.output_owners = {}

    async def reset(self):
        self.put(clk=0, rst=1)
        for name in self.INPUTS:
            self.put(**{name: 0})
        self.configure(self.descriptor(1, 1, 1, 0))
        for gate in self.GATES:
            self.put(**{gate: 1})
        self.fixed.clear()
        for channel in self.channels:
            channel.pause = False
        for _ in range(5):
            await tick(self.dut)
        self.put(rst=0)
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
        for _ in range(3):
            await self.step()
        assert self.value('job_ready') and not self.value('fatal')

    @staticmethod
    def descriptor(m, n, k, mode):
        return dict(m=m, n=n, k=k, mode=mode, a_base=0xfc0, bt_base=0x200fc0, c_base=0x400fc0,
                    a_stride=((k+63)//64)*64+64,
                    bt_stride=((k+63)//64)*64+128,
                    c_stride=((4*n+63)//64)*64+64)

    def configure(self, descriptor):
        self.put(**{'cfg_'+name: value for name, value in descriptor.items()})

    async def step(self, **values):
        # Copy scheduler offers before the sampled edge. The base monitor
        # independently handles AXI responses, local metadata and held words.
        from cocotb.triggers import Timer
        self.put(**values)
        await Timer(1, unit='ps')
        extra = {}
        for direction in ('load', 'store'):
            fields = ('bt', 'buf', 'base', 'stride', 'rows', 'row_bytes') if direction == 'load' else ('buf', 'base', 'stride', 'rows', 'row_bytes')
            extra[direction+'_fields'] = fields
            extra[direction+'_offer'] = tuple(self.value(direction+'_req_'+field) for field in fields)
        extra['core'] = tuple(self.value(name) for name in ('compute_m', 'compute_n', 'compute_k', 'compute_input_buf', 'compute_output_buf'))
        # Gating changes only READY/VALID admission, never saved descriptor fields.
        extra['compute_start'] = self.value('compute_start')
        s = await super().step()
        # compute_start can change with allow_compute in the base step; sample
        # actual launch from busy rising, whose inputs were the saved offer above.
        core_fire = not s['compute_busy'] and self.value('compute_busy')
        stop = s['fatal'] or s['mem_fatal'] or self.value('fatal')
        for direction in ('load', 'store'):
            valid, ready = s[direction+'_req_valid'], s[direction+'_req_ready']
            payload = extra[direction+'_offer']
            if direction in self.request_held and not stop:
                assert valid and payload == self.request_held[direction], f'{direction} descriptor changed under backpressure'
            if valid and not ready and not stop:
                self.request_held[direction] = payload
            else:
                self.request_held.pop(direction, None)
            if valid and ready:
                self.operation_logs[direction+'_req'].append((self.cycle, payload))
                if self.model is not None:
                    self.accept_descriptor(direction, payload)
        for direction in ('load', 'store'):
            if s[direction+'_done_valid'] and s[direction+'_done_ready'] and self.model is not None:
                self.complete_descriptor(direction, s[direction+'_done_status'])
        if core_fire:
            self.operation_logs['core'].append((self.cycle, extra['core']))
            if self.model is not None:
                r, c, k, ib, ob = extra['core']
                assert self.core_owner is None and ob not in self.output_owners, 'Live C buffer reused'
                owner = self.input_owners[ib]
                assert owner['loaded'] == {0, 1} and owner['tag'] == self.model['compute_cursor']
                tile = self.model['tiles'][owner['tag']]
                assert (r, c, k) == (tile['r'], tile['c'], self.model['desc']['k'])
                self.output_owners[ob] = dict(tag=owner['tag'], state='COMPUTING')
                self.core_owner = dict(tag=owner['tag'], ib=ib, ob=ob)
                self.model['compute_cursor'] += 1
                COVERAGE['independent_buffer_ids'] += int(ib != ob)
        if self.core_owner is not None and self.value('compute_done') and not self.value('compute_busy'):
            owner = self.core_owner
            self.input_owners.pop(owner['ib'])
            self.output_owners[owner['ob']]['state'] = 'READY'
            self.operation_logs['core_done'].append((self.cycle, owner['tag']))
            self.core_owner = None
        load_active = bool(self.load_owner)
        store_active = bool(self.store_owner)
        compute_active = bool(self.core_owner)
        if self.model is not None:
            if self.model['desc']['mode'] == 0 and not stop:
                assert int(load_active)+int(store_active)+int(compute_active) <= 1, 'MODE0 overlapped stages'
            COVERAGE['scheduler_load_overlap_edges'] += int(load_active and compute_active)
            COVERAGE['scheduler_store_overlap_edges'] += int(store_active and compute_active)
            COVERAGE['scheduler_three_way_edges'] += int(load_active and store_active and compute_active)
        if self.value('done'):
            assert not self.value('busy') and self.value('axi_quiescent') and self.value('local_idle')
            assert not self.value('fatal') and not self.value('compute_busy')
        return s

    def accept_descriptor(self, direction, payload):
        d = self.model['desc']
        if direction == 'load':
            assert self.load_owner is None
            bt, buf, base, stride, rows, row_bytes = payload
            ordinal = self.model['load_cursor']
            tag, expected_bt = divmod(ordinal, 2)
            tile = self.model['tiles'][tag]
            assert bt == expected_bt
            expected_base = d['bt_base']+tile['j']*d['bt_stride'] if bt else d['a_base']+tile['i']*d['a_stride']
            assert (base, stride, rows, row_bytes) == (expected_base, d['bt_stride'] if bt else d['a_stride'], tile['c'] if bt else tile['r'], d['k'])
            if not bt:
                assert buf not in self.input_owners, 'Live operand set overwritten'
                if d['mode'] == 0:
                    assert tag == self.model['retire_cursor'], 'Serial ingress outran store retirement'
                self.input_owners[buf] = dict(tag=tag, loaded=set())
            else:
                assert self.input_owners[buf]['tag'] == tag and self.input_owners[buf]['loaded'] == {0}
            self.load_owner = dict(tag=tag, buf=buf, bt=bt)
            self.model['load_cursor'] += 1
        else:
            assert self.store_owner is None
            buf, base, stride, rows, row_bytes = payload
            owner = self.output_owners[buf]
            tag = self.model['retire_cursor']
            assert owner['state'] == 'READY' and owner['tag'] == tag
            tile = self.model['tiles'][tag]
            assert (base, stride, rows, row_bytes) == (d['c_base']+tile['i']*d['c_stride']+4*tile['j'], d['c_stride'], tile['r'], 4*tile['c'])
            owner['state'] = 'WRITING'
            self.store_owner = dict(tag=tag, buf=buf)

    def complete_descriptor(self, direction, status):
        if direction == 'load':
            assert self.load_owner is not None
            owner = self.load_owner
            if status == 0:
                self.input_owners[owner['buf']]['loaded'].add(owner['bt'])
            self.load_owner = None
        else:
            assert self.store_owner is not None
            owner = self.store_owner
            if status == 0:
                assert self.output_owners[owner['buf']]['state'] == 'WRITING'
                self.output_owners.pop(owner['buf'])
                self.model['retire_cursor'] += 1
                self.operation_logs['retire'].append((self.cycle, owner['tag']))
            self.store_owner = None

    def images(self, d):
        m, n, k = (d[key] for key in ('m', 'n', 'k'))
        a, bt, golden = self.matrices(m, n, k)
        images = []
        self.initial_images = []
        for name, matrix in (('a', a), ('bt', bt)):
            region = bytearray(self.rng.randrange(1, 256) for _ in range(len(matrix)*d[name+'_stride']+128))
            for row, values in enumerate(matrix):
                region[64+row*d[name+'_stride']:64+row*d[name+'_stride']+k] = bytes(value & 255 for value in values)
            self.ram.write(d[name+'_base']-64, region)
            self.initial_images.append((d[name+'_base']-64, bytes(region)))
            images.append((name, d[name+'_base']-64, region))
        region = bytearray(self.rng.randrange(256) for _ in range(m*d['c_stride']+128))
        expected = bytearray(region)
        for row in range(m):
            for col in range(n):
                expected[64+row*d['c_stride']+4*col:68+row*d['c_stride']+4*col] = golden[row][col].to_bytes(4, 'little', signed=True)
        self.ram.write(d['c_base']-64, region)
        self.initial_images.append((d['c_base']-64, bytes(region)))
        images.append(('c', d['c_base']-64, expected))
        return images

    async def launch_job(self, d):
        assert self.value('job_ready')
        self.model = dict(desc=d.copy(), load_cursor=0, compute_cursor=0, retire_cursor=0,
                          tiles=[dict(i=i, j=j, r=min(self.t, d['m']-i), c=min(self.t, d['n']-j))
                                 for i in range(0, d['m'], self.t) for j in range(0, d['n'], self.t)])
        self.load_owner = self.store_owner = self.core_owner = None
        self.input_owners.clear()
        self.output_owners.clear()
        self.configure(d)
        await self.step(job_valid=1)
        self.put(job_valid=0)
        self.operation_logs['job'].append(self.cycle)
        # Interface promises a snapshot; live configuration is irrelevant afterward.
        self.configure({key: 0 for key in d})
        assert self.value('busy') and not self.value('done')

    async def check_job(self, d, images, mark):
        await self.until(lambda s: self.value('done'), limit=300000)
        assert self.model['load_cursor'] == 2*len(self.model['tiles'])
        assert self.model['compute_cursor'] == self.model['retire_cursor'] == len(self.model['tiles'])
        assert not self.input_owners and not self.output_owners and self.core_owner is None
        assert not self.value('load_busy') and not self.value('store_busy')
        expected_ar, expected_aw, expected_strobes, expected_last = [], [], [], []
        for tile in self.model['tiles']:
            for name, origin, rows in (('a', tile['i'], tile['r']), ('bt', tile['j'], tile['c'])):
                expected_ar += dma_test.bursts(d[name+'_base']+origin*d[name+'_stride'], d[name+'_stride'], rows, d['k'])
            tile_bursts = dma_test.bursts(d['c_base']+tile['i']*d['c_stride']+4*tile['j'], d['c_stride'], tile['r'], 4*tile['c'])
            expected_aw += tile_bursts
            expected_strobes += [15 if tile['c'] % 2 and pair == tile['c']//2 else 255
                                 for row in range(tile['r']) for pair in range((tile['c']+1)//2)]
            expected_last += [int(index == length-1) for _, length in tile_bursts for index in range(length)]
        assert [(x[0], x[1]+1) for x in self.logs['ar'][mark['ar']:]] == expected_ar
        assert [(x[0], x[1]+1) for x in self.logs['aw'][mark['aw']:]] == expected_aw
        assert [x[1] for x in self.logs['w'][mark['w']:]] == expected_strobes
        assert [x[2] for x in self.logs['w'][mark['w']:]] == expected_last
        assert len(self.logs['b'])-mark['b'] == len(expected_aw)
        for name, address, expected in images:
            assert self.ram.read(address, len(expected)) == expected, f'{name} matrix/padding/guards changed'
        await self.idle(4)
        assert self.value('done') and self.value('job_ready')
        COVERAGE['jobs'] += 1
        COVERAGE['output_values'] += d['m']*d['n']
        COVERAGE['macrotiles'] += len(self.model['tiles'])
        COVERAGE['modes'] = sorted(set(COVERAGE['modes']+[d['mode']]))
        COVERAGE['shapes'].append([d['m'], d['n'], d['k'], d['mode']])
        COVERAGE['case_results'].append(dict(shape=[d['m'], d['n'], d['k']], mode=d['mode'],
            macrotiles=len(self.model['tiles']),
            input_image_sha256_bytes={name: hashlib.sha256(expected).hexdigest()
                                      for name, _, expected in images if name != 'c'},
            read_bursts=len(expected_ar),write_bursts=len(expected_aw),
            write_beats=len(expected_strobes),write_valid_bytes=4*d['m']*d['n']))

    def mark(self):
        return {key: len(self.logs[key]) for key in ('ar', 'aw', 'w', 'b')}

    async def fatal_drained(self, code):
        await self.until(lambda s: self.value('fatal') and not self.value('busy'), limit=100000)
        assert self.value('fatal_code') == code and not self.value('done') and not self.value('job_ready')
        assert self.value('local_idle') and self.value('axi_quiescent') and not self.value('compute_busy')
        assert not self.external_reads and not self.local_reads
        assert len(self.logs['rd_cmd']) == len(self.logs['rd_done'])
        assert len(self.logs['wr_cmd']) == len(self.logs['wr_done'])


@cocotb.test()
async def complete_jobs_both_modes(dut):
    h = Harness(dut)
    await h.reset()
    shapes = [(1, 1, 1), (h.t+1, h.t+3, 9), (2*h.t+1, h.t-1, 33),
              (h.t-1, 2*h.t+1, 255), (h.t+1, h.t+1, 256)]
    for index, shape in enumerate(shapes):
        images = h.images(h.descriptor(*shape, 0))
        for mode in (0, 1):
            d = h.descriptor(*shape, mode)
            for address, initial in h.initial_images:
                h.ram.write(address, initial)
            mark = h.mark()
            await h.launch_job(d)
            if index == 1 and mode == 1:
                # Hold the next tile in its separately allocated input set
                # until the first C set retires. Its launch must then use the
                # newly free C set, proving that the two IDs are independent.
                await h.until(lambda s: h.model['load_cursor'] == 2 and
                              s['load_done_valid'] and s['load_done_ready'])
                h.fixed['allow_load_done'] = 0
                await h.until(lambda s: h.model['retire_cursor'] == 1)
                assert h.model['compute_cursor'] == 1 and not h.output_owners
                h.fixed['allow_load_done'] = 1
                await h.until(lambda s: h.model['compute_cursor'] == 2)
                second_launch = h.operation_logs['core'][-1][1]
                assert second_launch[3] != second_launch[4], 'Directed launch paired independent buffer IDs'
            await h.check_job(d, images, mark)
            dut._log.info('Checked M=%d N=%d K=%d MODE=%d: %d outputs, %d macrotiles',
                          *shape, mode, shape[0]*shape[1], len(h.model['tiles']))
    assert COVERAGE['scheduler_three_way_edges'] > 0
    assert COVERAGE['independent_buffer_ids'] > 0
    save_coverage('row_major_both_modes_tails_signed_padding_snapshot')


@cocotb.test()
async def blocked_store_ownership_and_completion(dut):
    h = Harness(dut, random_stalls=False)
    await h.reset()
    d = h.descriptor(2*h.t+1, 2*h.t+1, 9, 1)
    images, mark = h.images(d), h.mark()
    h.fixed['allow_store_done'] = 0
    await h.launch_job(d)
    await h.until(lambda s: s['store_done_valid'] and not s['store_done_ready'])
    await h.until(lambda s: len(h.operation_logs['core_done']) == 2)
    await h.idle(30)
    assert len(h.operation_logs['core']) == 2, 'Third launch overwrote an owned output set'
    assert len(h.output_owners) == 2 and h.store_owner is not None
    assert not h.value('done') and not h.value('job_ready')
    h.fixed['allow_store_done'] = 1
    await h.check_job(d, images, mark)
    COVERAGE['held_completion_checks'] += 1
    # Final store retirement alone cannot hand ownership back to the host.
    for hold in ('hold_local_idle', 'hold_axi_quiescent'):
        d = h.descriptor(1, 1, 1, 1)
        images, mark = h.images(d), h.mark()
        h.put(**{hold: 1})
        # Hold begins only after the accepted job; idle gates reject START too.
        h.put(**{hold: 0})
        await h.launch_job(d)
        h.put(**{hold: 1})
        await h.until(lambda s: h.model['retire_cursor'] == 1)
        await h.idle(12)
        assert h.value('busy') and not h.value('done') and not h.value('job_ready')
        h.put(**{hold: 0})
        await h.check_job(d, images, mark)
        COVERAGE['held_completion_checks'] += 1
    save_coverage('both_result_sets_retained_final_local_and_axi_idle')


@cocotb.test()
async def faults_drain_all_owners(dut):
    h = Harness(dut, random_stalls=False)
    for source, code in (('read', 7), ('write', 7), ('compute', 8), ('external', 9)):
        await h.reset()
        d = h.descriptor(2*h.t+1, h.t+1, 33, 1)
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
            if source == 'compute':
                h.put(inject_compute_error=1)
            else:
                h.put(inject_scheduler_fatal=1, inject_scheduler_code=9)
        await h.fatal_drained(code)
        h.put(inject_compute_error=0, inject_scheduler_fatal=0, inject_scheduler_code=0,
              inject_mem_fatal=1, inject_mem_code=10, job_valid=1)
        await h.idle(5)
        assert h.value('fatal_code') == code and not h.value('job_ready') and not h.value('done')
        h.put(job_valid=0)
        COVERAGE['fatal_cases'].append(source)
    # A later platform fault invalidates a previously completed result too.
    await h.reset()
    d = h.descriptor(1, 1, 1, 1)
    images, mark = h.images(d), h.mark()
    await h.launch_job(d)
    await h.check_job(d, images, mark)
    h.put(inject_scheduler_fatal=1, inject_scheduler_code=10)
    await h.step()
    assert h.value('fatal') and not h.value('done')
    await h.fatal_drained(10)
    COVERAGE['fatal_cases'].append('idle_after_success')
    # Fault and successful final terminal on the same edge cannot close a job.
    await h.reset()
    d = h.descriptor(1, 1, 1, 1)
    h.images(d)
    h.fixed['allow_store_done'] = 0
    await h.launch_job(d)
    await h.until(lambda s: s['store_done_valid'])
    h.fixed['allow_store_done'] = 1
    h.put(inject_scheduler_fatal=1, inject_scheduler_code=10)
    await h.fatal_drained(10)
    COVERAGE['fatal_cases'].append('final_terminal_same_edge')
    # Check the public maximum descriptor's one-past-end tile-count width
    # without presenting this admission-only test as a million-output GEMM.
    await h.reset()
    d = h.descriptor(1024, 1024, 1, 1)
    await h.launch_job(d)
    count = int(h.raw.scheduler.total_tiles.value)
    assert count == (1024//h.t)**2
    COVERAGE['maximum_tile_counts'].append(count)
    h.put(inject_scheduler_fatal=1, inject_scheduler_code=9)
    await h.fatal_drained(9)
    assert not h.logs['ar'] and not h.logs['aw']
    save_coverage('read_write_core_external_fault_drain_first_code')
