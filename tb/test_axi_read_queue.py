"""Ordered four-slot read checks at public local and AXI handshakes only."""
from collections import deque
import json
import os
from pathlib import Path
import random

import cocotb

from test_axi_burst import Harness


COVERAGE = dict(commands=0, valid_commands=0, invalid_commands=0, read_beats=0,
                delivered_words=0, completions=0, lengths=[], local_occupancy=[],
                axi_outstanding=[], scenarios={}, error_slots={}, simultaneous={},
                reset_discarded_commands=0)


def hit(group, key):
    COVERAGE[group][key] = COVERAGE[group].get(key, 0)+1


def save():
    path = os.environ.get('GEMM_READ_QUEUE_COVERAGE')
    if path:
        Path(path).write_text(json.dumps(COVERAGE, indent=2)+'\n')


def payload(tag, beats):
    # Distinct signed-byte patterns and word positions expose stale-slot data.
    return [int.from_bytes(bytes((tag*29+index*17+lane*61) & 255 for lane in range(8)), 'little')
            for index in range(beats)]


class QueueHarness(Harness):
    def __init__(self, dut):
        super().__init__(dut)
        assert self.read_slots == 4, 'This suite requires READ_SLOTS=4 and GEMM_READ_SLOTS=4'
        self.specs, self.owners, self.accepted, self.axi_pending = {}, deque(), [], deque()
        self.ar_specs = []
        self.write_owned = False

    async def reset(self):
        self.specs, self.owners, self.accepted, self.axi_pending = {}, deque(), [], deque()
        self.ar_specs = []
        self.write_owned = False
        await super().reset()
        assert self.value('local_idle')

    def expect(self, address, beats, tag, words=None, status=0, ar=True):
        assert tag not in self.specs
        spec = dict(address=address, beats=beats, tag=tag,
                    words=list(payload(tag, beats) if words is None and status == 0 else words or []),
                    status=status, ar_expected=ar, issued=False, response_closed=False, delivered=0)
        self.specs[tag] = spec
        return spec

    async def submit(self, address, beats, tag, words=None, status=0, ar=True):
        spec = self.expect(address, beats, tag, words, status, ar)
        await self.command('rd', address, beats, tag)
        return spec

    def fail(self, spec, status=8, cancel=False):
        assert spec['delivered'] == 0, 'Test must not revise an already delivered result'
        spec['status'], spec['words'] = status, []
        if cancel:
            assert not spec['issued']
            spec['ar_expected'] = False

    async def step(self, **signals):
        sample = await super().step(**signals)
        data = sample['rd_data_valid'] and sample['rd_data_ready']
        done = sample['rd_done_valid'] and sample['rd_done_ready']
        accept = sample['rd_cmd_valid'] and sample['rd_cmd_ready']
        ar = sample['m_axi_arvalid'] and sample['m_axi_arready']
        response = sample['m_axi_rvalid'] and sample['m_axi_rready']
        if data:
            assert self.owners, 'Local data without command ownership'
            spec = self.owners[0]
            index = spec['delivered']
            assert spec['status'] == 0 and spec['response_closed'], 'Unvalidated/failed burst published data'
            assert index < len(spec['words'])
            expected = spec['words'][index], index, int(index == len(spec['words'])-1), spec['tag']
            actual = tuple(sample[name] for name in ('rd_data', 'rd_data_index', 'rd_data_last', 'rd_data_tag'))
            assert actual == expected, (actual, expected)
            spec['delivered'] += 1
            COVERAGE['delivered_words'] += 1
        if done:
            assert self.owners, 'Duplicate or unowned completion'
            spec = self.owners.popleft()
            assert (sample['rd_done_status'], sample['rd_done_tag']) == (spec['status'], spec['tag'])
            assert spec['delivered'] == len(spec['words'])
            assert spec['response_closed'] or not spec['ar_expected'], 'Completion preceded response obligation'
            COVERAGE['completions'] += 1
        if accept:
            tag = sample['rd_cmd_tag']
            spec = self.specs[tag]
            assert spec not in self.accepted and len(self.owners) < 4, 'Read slot reused before ordered DONE'
            assert (sample['rd_cmd_addr'], sample['rd_cmd_beats']) == (spec['address'], spec['beats'])
            self.owners.append(spec)
            self.accepted.append(spec)
            COVERAGE['commands'] += 1
            COVERAGE['invalid_commands' if spec['status'] == 3 else 'valid_commands'] += 1
            if spec['status'] != 3 and spec['beats'] not in COVERAGE['lengths']:
                COVERAGE['lengths'].append(spec['beats'])
        # Only the response FIFO as it existed before this edge owns an R beat.
        # A simultaneous new AR is appended afterwards, never used retroactively.
        if response and self.axi_pending:
            if sample['m_axi_rlast']:
                self.axi_pending.popleft()['response_closed'] = True
            COVERAGE['read_beats'] += 1
        if ar:
            candidates = [spec for spec in self.accepted if spec['ar_expected'] and not spec['issued']]
            assert candidates, 'Unexpected AR'
            spec = candidates[0]
            assert (sample['m_axi_araddr'], sample['m_axi_arlen']+1) == (spec['address'], spec['beats'])
            spec['issued'] = True
            self.axi_pending.append(spec)
            self.ar_specs.append(spec)
        if sample['wr_done_valid'] and sample['wr_done_ready']:
            assert self.write_owned
            self.write_owned = False
        if sample['wr_cmd_valid'] and sample['wr_cmd_ready']:
            assert not self.write_owned
            self.write_owned = True
        assert len(self.axi_pending) == self.read_outstanding
        assert bool(self.value('local_idle')) == (not self.owners and not self.write_owned)
        for key, value in (('local_occupancy', len(self.owners)), ('axi_outstanding', self.read_outstanding)):
            if value not in COVERAGE[key]:
                COVERAGE[key].append(value)
        if done and accept:
            hit('simultaneous', 'retire_and_allocate')
        if ar and response and sample['m_axi_rlast']:
            hit('simultaneous', 'new_ar_and_old_rlast')
        return sample

    async def reply(self, words, error_index=None, response=2, ident=0):
        for index, word in enumerate(words):
            await self.read_beat(word, int(index == len(words)-1), response if index == error_index else 0, ident)

    async def drained(self, limit=5000):
        for _ in range(limit):
            if not self.owners and not self.write_owned:
                assert self.value('local_idle') and self.value('axi_quiescent')
                return
            await self.step()
        raise AssertionError('Ordered read queue did not drain')


@cocotb.test()
async def four_slots_reserve_until_ordered_done(dut):
    h = QueueHarness(dut)
    await h.reset()
    h.put(m_axi_arready=1, rd_data_ready=0, rd_done_ready=0)
    specs = [await h.submit(0x1000+index*0x100, beats, 100+index)
             for index, beats in enumerate((16, 15, 2, 1))]
    await h.wait_count(h.ar, 4)
    assert h.read_outstanding == 4 and not h.r
    fifth = h.expect(0x2000, 3, 104)
    h.put(rd_cmd_valid=1, rd_cmd_addr=fifth['address'], rd_cmd_beats=3, rd_cmd_tag=104)
    for _ in range(12):
        sample = await h.step()
        assert not sample['rd_cmd_ready']
    for spec in specs:
        await h.reply(spec['words'])
    await h.idle(12)
    assert h.read_outstanding == 0 and h.value('axi_quiescent') and not h.value('local_idle')
    assert not h.reads and not h.read_done and len(h.ar) == 4
    assert h.value('rd_data_valid') and not h.value('rd_cmd_ready')
    h.put(rd_data_ready=1)
    await h.wait_value('rd_done_valid')
    await h.idle(12)
    assert len(h.reads) == 16 and not h.read_done and not h.value('rd_cmd_ready')
    h.put(rd_done_ready=1)
    for _ in range(100):
        sample = await h.step()
        if sample['rd_cmd_ready']:
            assert sample['rd_cmd_valid']
            h.put(rd_cmd_valid=0)
            break
    else:
        raise AssertionError('Fifth command did not receive a released slot')
    await h.wait_count(h.ar, 5)
    await h.reply(fifth['words'])
    await h.drained()
    assert h.read_done == [(0, tag) for tag in range(100, 105)]
    hit('scenarios', 'four_ar_before_r_full_buffer_head_data_and_done_stalls')

    # Local ownership includes a partial write collection and held write DONE,
    # even when this master has no remaining AXI obligation.
    h.put(m_axi_awready=1, m_axi_wready=1, wr_done_ready=0)
    await h.command('wr', 0x3000, 2, 110)
    await h.feed([0x1234], [0xff])
    assert h.value('axi_quiescent') and not h.value('local_idle')
    await h.feed([0x5678], [0x0f])
    await h.wait_count(h.aw, 1)
    await h.wait_count(h.w, 2)
    assert not h.value('axi_quiescent')
    await h.step(m_axi_bvalid=1, m_axi_bresp=0, m_axi_bid=0)
    h.put(m_axi_bvalid=0)
    await h.wait_value('wr_done_valid')
    await h.idle(5)
    assert h.value('axi_quiescent') and not h.value('local_idle')
    h.put(wr_done_ready=1)
    await h.drained()
    hit('scenarios', 'local_idle_includes_write_collection_and_terminal')
    save()


@cocotb.test()
async def ordered_invalid_commands_and_simultaneous_edges(dut):
    h = QueueHarness(dut)
    await h.reset()
    h.put(m_axi_arready=1, rd_data_ready=0, rd_done_ready=0)
    first = await h.submit(0x4000, 3, 200)
    await h.submit(0x5001, 1, 201, status=3, ar=False)
    third = await h.submit(0x6000, 2, 202)
    await h.wait_count(h.ar, 2)
    await h.reply(first['words'])
    await h.reply(third['words'])
    await h.idle(8)
    assert not h.read_done and len(h.ar) == 2 and not h.value('fatal')
    h.put(rd_data_ready=1, rd_done_ready=1)
    await h.drained()
    assert h.read_done == [(0, 200), (3, 201), (0, 202)]
    hit('scenarios', 'valid_invalid_valid_ordered')

    # Repeat beyond multiple ring wraps; choose events by public VALID, never
    # by a DUT state encoding or a guessed pipeline latency.
    for iteration in range(8):
        tag = 210+iteration*3
        before = len(h.ar)
        a = await h.submit(0x8000+iteration*0x100, 1, tag)
        await h.wait_count(h.ar, before+1)
        h.put(m_axi_arready=0)
        b = await h.submit(0xa000+iteration*0x100, 2, tag+1)
        await h.wait_value('m_axi_arvalid')
        sample = await h.step(m_axi_arready=1, m_axi_rvalid=1, m_axi_rdata=a['words'][0],
                              m_axi_rresp=0, m_axi_rid=0, m_axi_rlast=1, rd_done_ready=0)
        assert sample['m_axi_arvalid'] and sample['m_axi_rready']
        h.put(m_axi_rvalid=0)
        await h.wait_value('rd_done_valid')
        c = h.expect(0xc000+iteration*0x100, 1, tag+2)
        sample = await h.step(rd_done_ready=1, rd_cmd_valid=1, rd_cmd_addr=c['address'],
                              rd_cmd_beats=1, rd_cmd_tag=tag+2)
        assert sample['rd_done_valid'] and sample['rd_cmd_ready']
        h.put(rd_cmd_valid=0)
        await h.wait_count(h.ar, before+3)
        await h.reply(b['words'])
        await h.reply(c['words'])
        await h.drained()
    assert COVERAGE['simultaneous']['retire_and_allocate'] >= 8
    assert COVERAGE['simultaneous']['new_ar_and_old_rlast'] >= 8
    hit('scenarios', 'ring_wrap')
    save()


@cocotb.test()
async def seeded_read_queue_backpressure(dut):
    h = QueueHarness(dut)
    await h.reset()
    rng = random.Random(20261003)
    commands = []
    for index in range(160):
        beats = index % 16+1
        address = (0x100+index)*4096+4096-8*beats
        invalid = index % 10 == 9
        commands.append(h.expect(address+int(invalid), beats, 1000+index,
                                 words=[rng.getrandbits(64) for _ in range(beats)] if not invalid else [],
                                 status=3 if invalid else 0, ar=not invalid))
    next_command, next_ar = 0, 0
    responses = deque()
    held_r = None
    for _ in range(30000):
        signals = dict(m_axi_arready=int(rng.randrange(4) != 0),
                       rd_data_ready=int(rng.randrange(3) != 0),
                       rd_done_ready=int(rng.randrange(4) != 0), m_axi_rvalid=0, rd_cmd_valid=0)
        if next_command < len(commands):
            spec = commands[next_command]
            signals.update(rd_cmd_valid=1, rd_cmd_addr=spec['address'],
                           rd_cmd_beats=spec['beats'], rd_cmd_tag=spec['tag'])
        if held_r is None and responses and rng.randrange(4) != 0:
            spec, index = responses[0]
            held_r = dict(m_axi_rvalid=1, m_axi_rdata=spec['words'][index], m_axi_rresp=0,
                          m_axi_rid=0, m_axi_rlast=int(index == spec['beats']-1))
        if held_r is not None:
            signals.update(held_r)
        sample = await h.step(**signals)
        if sample['rd_cmd_valid'] and sample['rd_cmd_ready']:
            next_command += 1
        if sample['m_axi_rvalid'] and sample['m_axi_rready']:
            spec, index = responses.popleft()
            if index+1 != spec['beats']:
                responses.appendleft((spec, index+1))
            held_r = None
        for spec in h.ar_specs[next_ar:]:
            responses.append((spec, 0))
        next_ar = len(h.ar_specs)
        if next_command == len(commands) and not h.owners:
            break
    else:
        raise AssertionError('Seeded four-slot queue failed to finish')
    h.put(rd_cmd_valid=0, m_axi_rvalid=0)
    assert len(h.read_done) == 160 and len(h.ar) == 144 and not h.value('fatal')
    assert not responses and h.value('axi_quiescent') and h.value('local_idle')
    assert set(COVERAGE['lengths']) == set(range(1, 17))
    assert set(COVERAGE['local_occupancy']) == set(range(5))
    assert set(COVERAGE['axi_outstanding']) == set(range(5))
    hit('scenarios', 'seeded_160_commands_144_valid_16_invalid')
    save()


@cocotb.test()
async def response_errors_and_framing_poison_each_slot(dut):
    h = QueueHarness(dut)
    for kind in ('rresp', 'rid', 'early_last', 'late_last'):
        for bad_slot in range(4):
            await h.reset()
            h.put(m_axi_arready=1, rd_data_ready=0, rd_done_ready=0)
            before_done, before_ar = len(h.read_done), len(h.ar)
            specs = [await h.submit(0x20000+index*0x100, 3, 2000+index)
                     for index in range(4)]
            await h.wait_count(h.ar, before_ar+4)
            incoming = [list(spec['words']) for spec in specs]
            for index in range(bad_slot):
                await h.reply(incoming[index])
            h.fail(specs[bad_slot], 7 if kind == 'rresp' else 8)
            if kind != 'rresp':
                for spec in specs[bad_slot+1:]:
                    h.fail(spec)
            if kind == 'early_last':
                await h.read_beat(incoming[bad_slot][0], 1)
            elif kind == 'late_last':
                for word in incoming[bad_slot]:
                    await h.read_beat(word, 0)
                await h.read_beat(0xbadbad, 1)
            else:
                for index, word in enumerate(incoming[bad_slot]):
                    await h.read_beat(word, int(index == 2),
                                      2 if kind == 'rresp' and index == 1 else 0,
                                      1 if kind == 'rid' and index == 1 else 0)
            for index in range(bad_slot+1, 4):
                await h.reply(incoming[index])
            await h.idle(12)
            assert h.value('fatal_code') == (7 if kind == 'rresp' else 8)
            assert h.value('axi_quiescent') and not h.value('local_idle')
            assert len(h.read_done) == before_done
            # Earlier complete bursts keep every offered/data/DONE payload;
            # framing errors suppress only the current and later unvalidated ones.
            h.put(rd_data_ready=1, rd_done_ready=1)
            await h.drained()
            assert h.read_done[before_done:] == [(spec['status'], spec['tag']) for spec in specs]
            hit('error_slots', f'{kind}_{bad_slot}')
    save()


@cocotb.test()
async def held_ar_shared_fault_and_first_error(dut):
    h = QueueHarness(dut)
    await h.reset()
    h.put(m_axi_arready=1, rd_data_ready=0, rd_done_ready=0)
    first = await h.submit(0x30000, 2, 3000)
    await h.wait_count(h.ar, 1)
    h.put(m_axi_arready=0)
    held = await h.submit(0x31000, 2, 3001)
    await h.wait_value('m_axi_arvalid')
    canceled = await h.submit(0x32000, 2, 3002)
    invalid = await h.submit(0x33001, 1, 3003, status=3, ar=False)
    first_words = list(first['words'])
    h.fail(first, 7)
    h.fail(canceled, 8, cancel=True)
    await h.reply(first_words, error_index=0)
    await h.idle(20)
    assert h.value('m_axi_arvalid') and not h.value('axi_quiescent') and len(h.ar) == 1
    h.put(m_axi_arready=1)
    await h.wait_count(h.ar, 2)
    await h.reply(held['words'])
    h.put(rd_data_ready=1, rd_done_ready=1)
    await h.drained()
    assert h.read_done == [(7, 3000), (0, 3001), (8, 3002), (3, 3003)]
    assert len(h.ar) == 2
    hit('scenarios', 'held_ar_survives_fault_unoffered_cancels_invalid_retains_bad_desc')

    await h.reset()
    h.put(m_axi_arready=1)
    first_ar = len(h.ar)
    a = await h.submit(0x38000, 1, 3050)
    await h.wait_count(h.ar, first_ar+1)
    h.fail(a, 7)
    rejected = h.expect(0x39000, 1, 3051, status=8, ar=False)
    sample = await h.step(rd_cmd_valid=1, rd_cmd_addr=rejected['address'], rd_cmd_beats=1,
                          rd_cmd_tag=3051, m_axi_rvalid=1, m_axi_rdata=1, m_axi_rresp=2,
                          m_axi_rlast=1, m_axi_rid=0)
    assert sample['rd_cmd_ready'] and sample['m_axi_rready']
    h.put(rd_cmd_valid=0, m_axi_rvalid=0)
    await h.drained()
    assert h.read_done[-2:] == [(7, 3050), (8, 3051)] and len(h.ar) == first_ar+1
    hit('scenarios', 'same_edge_read_command_and_fault_cancels_without_ar')

    # A write error does not poison the framing of already issued reads.
    await h.reset()
    before = len(h.read_done)
    first_ar = len(h.ar)
    h.put(m_axi_arready=1, m_axi_awready=1, m_axi_wready=1, rd_data_ready=0, rd_done_ready=0)
    specs = [await h.submit(0x40000+index*0x100, 2, 3100+index) for index in range(4)]
    await h.wait_count(h.ar, first_ar+4)
    await h.reply(specs[0]['words'])
    await h.wait_value('rd_data_valid')
    await h.command('wr', 0x48000, 1, 3200)
    await h.feed([0x1122334455667788], [0x55])
    await h.wait_count(h.aw, 1)
    await h.wait_count(h.w, 1)
    await h.step(m_axi_bvalid=1, m_axi_bresp=2, m_axi_bid=0)
    h.put(m_axi_bvalid=0)
    await h.idle(10)
    for spec in specs[1:]:
        await h.reply(spec['words'])
    h.put(rd_data_ready=1, rd_done_ready=1)
    await h.drained()
    assert h.read_done[before:] == [(0, 3100+index) for index in range(4)]
    assert h.write_done[-1] == (7, 3200) and h.value('fatal_code') == 7
    hit('scenarios', 'shared_write_fault_preserves_issued_and_published_reads')

    # First own error remains MEM_RESP even if later missing LAST poisons the
    # stream; a later issued slot receives PROTOCOL and publishes no data.
    await h.reset()
    h.put(m_axi_arready=1)
    before = len(h.ar)
    a = await h.submit(0x50000, 2, 3300)
    b = await h.submit(0x51000, 1, 3301)
    await h.wait_count(h.ar, before+2)
    h.fail(a, 7); h.fail(b, 8)
    await h.read_beat(11, 0, 2)
    await h.read_beat(12, 0)
    await h.read_beat(13, 1)
    await h.read_beat(14, 1)
    await h.drained()
    assert h.read_done[-2:] == [(7, 3300), (8, 3301)] and h.value('fatal_code') == 7
    hit('scenarios', 'first_own_error_precedes_later_stream_poison')
    save()


@cocotb.test()
async def missing_rlast_keeps_each_issued_obligation(dut):
    h = QueueHarness(dut)
    await h.reset()
    h.put(m_axi_arready=1)
    specs = [await h.submit(0x60000+index*0x100, 1, 4000+index) for index in range(4)]
    await h.wait_count(h.ar, 4)
    for spec in specs:
        h.fail(spec)
    for index in range(35):
        await h.read_beat(index, 0)
    await h.idle(10)
    assert h.value('fatal_code') == 8 and h.read_outstanding == 4
    assert not h.reads and not h.read_done and not h.value('axi_quiescent')
    # A later LAST closes exactly one outstanding obligation. It cannot be
    # interpreted as completing all the other requests already accepted by AXI.
    await h.read_beat(100, 1)
    await h.idle(8)
    assert h.read_outstanding == 3 and len(h.read_done) == 1 and not h.value('axi_quiescent')
    await h.read_beat(101, 1)
    await h.read_beat(102, 1)
    await h.idle(12)
    assert h.read_outstanding == 1 and len(h.read_done) == 3 and not h.value('axi_quiescent')
    assert not h.value('local_idle') and h.value('m_axi_rready') and not h.reads
    await h.read_beat(103, 1)
    await h.drained()
    assert h.read_done == [(8, 4000+index) for index in range(4)]
    hit('scenarios', 'missing_last_drain_preserves_four_distinct_obligations')
    save()


@cocotb.test()
async def coordinated_reset_discards_four_mixed_slots(dut):
    h = QueueHarness(dut)
    await h.reset()
    h.put(m_axi_arready=1, rd_data_ready=0, rd_done_ready=0)
    first = await h.submit(0x70000, 2, 5000)
    second = await h.submit(0x71000, 3, 5001)
    await h.wait_count(h.ar, 2)
    await h.reply(first['words'])
    await h.read_beat(second['words'][0], 0)
    h.put(m_axi_arready=0)
    held = await h.submit(0x72000, 4, 5002)
    await h.wait_value('m_axi_arvalid')
    await h.submit(0x73000, 1, 5003)
    await h.idle(8)
    # Public events establish four distinct lifetimes: validated head data,
    # an accepted AR with a partial response, held AR, and unoffered work.
    assert len(h.owners) == 4 and len(h.ar) == 2 and h.read_outstanding == 1
    assert h.value('rd_data_valid') and h.value('rd_data_tag') == first['tag']
    assert h.value('rd_data') == first['words'][0]
    assert h.value('m_axi_arvalid') and h.value('m_axi_araddr') == held['address']
    assert not h.reads and not h.read_done and not h.value('local_idle')
    COVERAGE['reset_discarded_commands'] += len(h.owners)

    # This resets the pin-level memory responder as well as the master: old
    # responses are discarded, not injected into a new transaction epoch.
    # It models coordinated reset, not a master-only AXI abort.
    before_ar = len(h.ar)
    await h.reset()
    await h.idle(8)
    assert not h.reads and not h.read_done and len(h.ar) == before_ar
    assert h.value('local_idle') and h.value('axi_quiescent')
    h.put(m_axi_arready=1, rd_data_ready=0, rd_done_ready=0)
    fresh = []
    for index, beats in enumerate((2, 3, 4, 1)):
        # Reuse the addresses and tags but change every word, exposing stale
        # buffer publication even if its metadata survived incorrectly.
        words = [word ^ 0xffffffffffffffff for word in payload(5000+index, beats)]
        fresh.append(await h.submit(0x70000+index*0x1000, beats, 5000+index, words=words))
    await h.wait_count(h.ar, before_ar+4)
    assert h.read_outstanding == 4
    for spec in fresh:
        await h.reply(spec['words'])
    h.put(rd_data_ready=1, rd_done_ready=1)
    await h.drained()
    assert h.read_done == [(0, 5000+index) for index in range(4)]
    assert len(h.reads) == 10 and not h.value('fatal')
    hit('scenarios', 'coordinated_reset_four_mixed_slots_then_fresh_four_reads')
    save()


@cocotb.test()
async def held_successful_done_survives_later_read_protocol_fault(dut):
    h = QueueHarness(dut)
    await h.reset()
    h.put(m_axi_arready=1, rd_data_ready=1, rd_done_ready=0)
    first = await h.submit(0x80000, 2, 5100)
    later = await h.submit(0x81000, 3, 5101)
    await h.wait_count(h.ar, 2)
    await h.reply(first['words'])
    await h.wait_value('rd_done_valid')
    assert len(h.reads) == 2 and not h.read_done
    expected_done = (h.value('rd_done_status'), h.value('rd_done_tag'))
    assert expected_done == (0, first['tag'])
    h.fail(later, 8)
    await h.read_beat(0xbad, 1)  # Early LAST in the later three-beat response.
    for _ in range(12):
        await h.step()
        assert h.value('rd_done_valid')
        assert (h.value('rd_done_status'), h.value('rd_done_tag')) == expected_done
    assert h.value('fatal_code') == 8 and not h.read_done and len(h.reads) == 2
    assert h.value('axi_quiescent') and not h.value('local_idle')
    h.put(rd_done_ready=1)
    await h.drained()
    assert h.read_done == [(0, 5100), (8, 5101)]
    hit('scenarios', 'held_successful_done_survives_later_read_protocol_fault')
    save()


@cocotb.test()
async def repeated_tags_and_consecutive_invalids_wrap_in_order(dut):
    # Tags are opaque payload, not unique transaction IDs. Use an ordinal
    # scoreboard rather than QueueHarness's convenient unique-tag lookup.
    h = Harness(dut)
    assert h.read_slots == 4
    await h.reset()
    rng = random.Random(20261004)
    specs = []
    for ordinal in range(72):
        beats = ordinal % 16+1
        invalid = ordinal % 6 in (1, 2, 3)
        address = (0x180+ordinal)*4096+4096-8*beats+int(invalid)
        tag = (0x1234, 0x1234, 0)[ordinal % 3]
        specs.append(dict(ordinal=ordinal, address=address, beats=beats, tag=tag,
                          status=3 if invalid else 0, delivered=0, response_closed=False,
                          words=[] if invalid else [rng.getrandbits(64) for _ in range(beats)]))
    owners, unissued, responses = deque(), deque(), deque()
    next_command = 0
    retired = []
    held_r = None
    for _ in range(20000):
        signals = dict(m_axi_arready=int(rng.randrange(4) != 0),
                       rd_data_ready=int(rng.randrange(3) != 0),
                       rd_done_ready=int(rng.randrange(4) != 0),
                       rd_cmd_valid=0, m_axi_rvalid=0)
        if next_command < len(specs):
            spec = specs[next_command]
            signals.update(rd_cmd_valid=1, rd_cmd_addr=spec['address'],
                           rd_cmd_beats=spec['beats'], rd_cmd_tag=spec['tag'])
        if held_r is None and responses and rng.randrange(4) != 0:
            spec, index = responses[0]
            held_r = dict(m_axi_rvalid=1, m_axi_rdata=spec['words'][index],
                          m_axi_rresp=0, m_axi_rid=0, m_axi_rlast=int(index+1 == spec['beats']))
        if held_r is not None:
            signals.update(held_r)
        sample = await h.step(**signals)
        data = sample['rd_data_valid'] and sample['rd_data_ready']
        done = sample['rd_done_valid'] and sample['rd_done_ready']
        accept = sample['rd_cmd_valid'] and sample['rd_cmd_ready']
        if data:
            assert owners, 'Repeated-tag data has no ordinal owner'
            spec = owners[0]
            index = spec['delivered']
            assert spec['status'] == 0 and spec['response_closed'] and index < spec['beats']
            assert tuple(sample[name] for name in ('rd_data', 'rd_data_index', 'rd_data_last', 'rd_data_tag')) == (
                spec['words'][index], index, int(index+1 == spec['beats']), spec['tag'])
            spec['delivered'] += 1
            COVERAGE['delivered_words'] += 1
        if done:
            assert owners, 'Repeated-tag completion has no ordinal owner'
            spec = owners.popleft()
            assert (sample['rd_done_status'], sample['rd_done_tag']) == (spec['status'], spec['tag'])
            assert spec['delivered'] == len(spec['words'])
            assert spec['status'] == 3 or spec['response_closed']
            retired.append(spec['ordinal'])
            COVERAGE['completions'] += 1
        if accept:
            spec = specs[next_command]
            assert len(owners) < 4
            assert (sample['rd_cmd_addr'], sample['rd_cmd_beats'], sample['rd_cmd_tag']) == (
                spec['address'], spec['beats'], spec['tag'])
            owners.append(spec)
            if spec['status'] == 0:
                unissued.append(spec)
            next_command += 1
            COVERAGE['commands'] += 1
            COVERAGE['invalid_commands' if spec['status'] == 3 else 'valid_commands'] += 1
        # Responses belong only to earlier accepted ARs, before this edge's AR.
        if sample['m_axi_rvalid'] and sample['m_axi_rready']:
            assert responses and held_r is not None
            spec, index = responses.popleft()
            if index+1 == spec['beats']:
                spec['response_closed'] = True
            else:
                responses.appendleft((spec, index+1))
            held_r = None
            COVERAGE['read_beats'] += 1
        if sample['m_axi_arvalid'] and sample['m_axi_arready']:
            assert unissued, 'An invalid command issued AR or a valid AR duplicated'
            spec = unissued.popleft()
            assert (sample['m_axi_araddr'], sample['m_axi_arlen']+1) == (spec['address'], spec['beats'])
            responses.append((spec, 0))
        assert len(responses) == h.read_outstanding
        assert bool(h.value('local_idle')) == (not owners)
        for key, value in (('local_occupancy', len(owners)), ('axi_outstanding', h.read_outstanding)):
            if value not in COVERAGE[key]:
                COVERAGE[key].append(value)
        if done and accept:
            hit('simultaneous', 'retire_and_allocate')
        if next_command == len(specs) and not owners:
            break
    else:
        raise AssertionError('Repeated tags/consecutive invalid commands did not drain')
    h.put(rd_cmd_valid=0, m_axi_rvalid=0)
    await h.idle(8)
    assert retired == list(range(72)) and not unissued and not responses
    assert len(h.ar) == 36 and len(h.read_done) == 72
    assert h.read_done == [(spec['status'], spec['tag']) for spec in specs]
    assert len(h.reads) == sum(len(spec['words']) for spec in specs)
    assert not h.value('fatal') and h.value('local_idle') and h.value('axi_quiescent')
    hit('scenarios', 'repeated_opaque_tags_consecutive_invalids_72_commands')
    save()


@cocotb.test()
async def external_cancel_preserves_held_ar_and_cancels_pending(dut):
    h = QueueHarness(dut)
    await h.reset()
    h.put(m_axi_arready=0, rd_data_ready=1, rd_done_ready=1)
    specs = [await h.submit(0x1000+index*0x100, 3, 900+index)
             for index in range(4)]
    await h.wait_value('m_axi_arvalid')
    held_address = h.value('m_axi_araddr')
    assert held_address == specs[0]['address'] and not h.ar
    for spec in specs[1:]:
        h.fail(spec, cancel=True)
    # A held VALID survives cancellation, including acceptance on the same
    # edge. Cancellation is sticky even if the caller later drops the input.
    sample = await h.step(rd_cancel=1, m_axi_arready=1)
    assert sample['m_axi_arvalid'] and sample['m_axi_araddr'] == held_address
    h.put(rd_cancel=0)
    await h.idle(16)
    assert len(h.ar) == 1 and not h.value('rd_cmd_ready')
    assert not h.value('fatal') and not h.read_done
    await h.reply(specs[0]['words'])
    await h.drained()
    assert h.read_done == [(0, 900), (8, 901), (8, 902), (8, 903)]
    assert len(h.ar) == 1 and len(h.reads) == 3
    assert not h.value('rd_cmd_ready') and not h.value('fatal')
    hit('scenarios', 'external_cancel_held_ar_three_pending_same_edge_accept')
    await h.reset()
    assert h.value('rd_cmd_ready')
    # Cancellation on the first promotion edge must not create an AR. Unlike
    # the earlier held-address case, this command has no AXI obligation yet.
    before_ar = len(h.ar)
    before_done = len(h.read_done)
    spec = await h.submit(0x3100, 3, 905)
    assert not h.value('m_axi_arvalid')
    h.fail(spec, cancel=True)
    await h.step(rd_cancel=1)
    h.put(rd_cancel=0)
    await h.drained()
    assert len(h.ar) == before_ar
    assert h.read_done[before_done:] == [(8, 905)]
    assert not h.value('rd_cmd_ready') and not h.value('fatal')
    hit('scenarios', 'external_cancel_before_first_ar_promotion')
    save()


@cocotb.test()
async def external_cancel_keeps_validated_data_and_done(dut):
    h = QueueHarness(dut)
    await h.reset()
    h.put(m_axi_arready=1, rd_data_ready=0, rd_done_ready=0)
    spec = await h.submit(0x2000, 2, 910)
    await h.wait_count(h.ar, 1)
    await h.reply(spec['words'])
    await h.wait_value('rd_data_valid')
    await h.idle(12, rd_cancel=1)
    assert h.value('rd_data_valid') and not h.reads and not h.value('fatal')
    h.put(rd_data_ready=1)
    await h.wait_value('rd_done_valid')
    await h.idle(12)
    assert h.value('rd_done_status') == 0 and not h.read_done
    h.put(rd_done_ready=1)
    await h.drained()
    assert h.read_done == [(0, 910)] and len(h.reads) == 2
    # Cancellation while an already successful terminal is stalled may not
    # revise its status or tag either.
    await h.reset()
    h.put(m_axi_arready=1, rd_done_ready=0)
    before = len(h.ar)
    spec = await h.submit(0x2100, 1, 911)
    await h.wait_count(h.ar, before+1)
    await h.reply(spec['words'])
    await h.wait_value('rd_done_valid')
    await h.idle(12, rd_cancel=1)
    assert h.value('rd_done_status') == 0
    h.put(rd_done_ready=1)
    await h.drained()
    assert h.read_done[-1] == (0, 911) and not h.value('fatal')
    hit('scenarios', 'external_cancel_held_validated_data_and_success_done')
    save()


@cocotb.test()
async def external_cancel_does_not_interrupt_write_collection(dut):
    h = QueueHarness(dut)
    await h.reset()
    h.put(m_axi_awready=1, m_axi_wready=1)
    await h.command('wr', 0x3000, 2, 920)
    await h.feed([0x12345678], [0xff])
    await h.idle(8, rd_cancel=1)
    assert not h.value('rd_cmd_ready') and h.value('wr_data_ready')
    assert not h.value('fatal') and not h.ar and not h.aw and not h.w
    h.put(rd_cancel=0)
    await h.feed([0xabcdef01], [0x0f])
    await h.wait_count(h.aw, 1)
    await h.wait_count(h.w, 2)
    await h.step(m_axi_bvalid=1, m_axi_bresp=0, m_axi_bid=0)
    h.put(m_axi_bvalid=0)
    await h.drained()
    assert h.write_done == [(0, 920)]
    assert h.w == [(0x12345678, 0xff, 0), (0xabcdef01, 0x0f, 1)]
    assert not h.value('rd_cmd_ready') and not h.value('fatal')
    assert not h.ar and not h.read_done
    hit('scenarios', 'external_cancel_read_admission_only_write_collection_unchanged')
    save()
