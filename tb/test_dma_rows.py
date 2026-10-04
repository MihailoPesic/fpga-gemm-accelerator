"""Row planning checks against independently enumerated byte addresses.

The completion responder is local: these tests do not qualify AXI traffic,
buffer ownership, data movement, or resetting a master with live transactions.
"""
from dataclasses import dataclass, replace
from collections import deque
import itertools
import json
import os
from pathlib import Path
import random

import cocotb
from common import tick


DDR_BYTES = 128 * 1024 * 1024
COVERAGE = {
    'scope': 'Row burst planning and ordered local request/completion handshakes; '
             'no AXI/data/ownership/reset-domain qualification',
    'read_slots': int(os.environ.get('GEMM_READ_SLOTS', '1')),
    'outstanding_counts': [], 'queued_scenarios': [], 'same_edge_issue_retire': 0,
    'valid_descriptors': 0, 'invalid_descriptors': 0,
    'random_valid_descriptors': 0, 'random_invalid_descriptors': 0,
    'burst_count': 0, 'read_operations': 0, 'write_operations': 0,
    'burst_lengths': [], 'row_counts': [], 'row_bytes': [],
    'final_strobes': [], 'completion_errors': {}, 'error_positions': [],
    'stalled_burst_cycles': 0, 'stalled_done_cycles': 0,
    'delayed_completion_cycles': 0, 'snapshot_checks': 0,
    'reset_states': [], 'directed_cases': [],
}


def cover_value(name, value):
    if value not in COVERAGE[name]:
        COVERAGE[name].append(value)
        COVERAGE[name].sort()


def save_coverage():
    path = os.environ.get('GEMM_COVERAGE')
    if path:
        Path(path).write_text(json.dumps(COVERAGE, indent=2) + '\n')


@dataclass(frozen=True)
class Descriptor:
    write: int
    base: int
    stride: int
    rows: int
    row_bytes: int

    def signals(self):
        return {f'req_{name}': getattr(self, name)
                for name in ('write', 'base', 'stride', 'rows', 'row_bytes')}


def expected_bursts(desc):
    """Enumerate every row word first, partition by page, then by burst size.

    Python integers keep address validation wider than any descriptor field.
    Each word's address and byte mask come directly from the row's useful bytes.
    No RTL counters, next-state expressions, or splitting recurrence are used.
    """
    if not 1 <= desc.rows <= 32 or not 1 <= desc.row_bytes <= 256:
        return None
    byte_offsets = list(range(0, desc.row_bytes, 8))
    storage_bytes = len(byte_offsets) * 8
    if (desc.base % 8 or desc.stride % 8 or desc.stride < storage_bytes
            or (desc.write and desc.row_bytes % 4)):
        return None
    if desc.base + (desc.rows - 1) * desc.stride + storage_bytes > DDR_BYTES:
        return None
    result = []
    for row in range(desc.rows):
        words = []
        for word, offset in enumerate(byte_offsets):
            useful_lanes = [lane for lane in range(8)
                            if offset + lane < desc.row_bytes]
            mask = sum(1 << lane for lane in useful_lanes) if desc.write else 255
            words.append((word, desc.base + row * desc.stride + offset, mask))
        for _, page_words in itertools.groupby(words, key=lambda item: item[1] // 4096):
            page_words = list(page_words)
            for start in range(0, len(page_words), 16):
                burst = page_words[start:start + 16]
                row_last = burst[-1][0] == len(words) - 1
                result.append({
                    'write': desc.write, 'addr': burst[0][1], 'beats': len(burst),
                    'row': row, 'word': burst[0][0], 'row_last': int(row_last),
                    'last': int(row_last and row == desc.rows - 1),
                    'final_strb': burst[-1][2],
                })
    return result


class Harness:
    INPUTS = ('req_valid', 'req_write', 'req_base', 'req_stride', 'req_rows',
              'req_row_bytes', 'burst_ready', 'complete_valid', 'complete_status',
              'done_ready', 'cancel', 'cancel_status')
    FIELDS = ('write', 'addr', 'beats', 'row', 'word', 'row_last', 'last', 'final_strb')

    def __init__(self, dut):
        self.dut = dut
        self.active = None
        self.outstanding = deque()
        self.read_slots = COVERAGE['read_slots']
        self.held = {}
        self.expected = []
        self.accepted = []
        self.result_status = None

    def put(self, **signals):
        for name, value in signals.items():
            getattr(self.dut, name).value = value

    def value(self, name):
        return int(getattr(self.dut, name).value)

    async def reset(self):
        # A local model reset deliberately discards local work. Integrators must
        # coordinate reset with the real burst engine/AXI platform separately.
        self.put(clk=0, rst=1, **{name: 0 for name in self.INPUTS})
        for _ in range(2):
            await tick(self.dut)
            assert not self.value('busy')
            assert not self.value('burst_valid') and not self.value('done_valid')
        self.active = None
        self.outstanding.clear()
        self.expected, self.accepted = [], []
        self.result_status = None
        self.held.clear()
        self.put(rst=0)
        await self.step()
        assert self.value('req_ready') and not self.value('busy')

    async def step(self, **signals):
        self.put(**signals)

        def snapshot():
            names = self.INPUTS + ('req_ready', 'busy', 'burst_valid',
                                   'complete_ready', 'done_valid')
            sample = {name: self.value(name) for name in names}
            sample['payload'] = ({field: self.value(f'burst_{field}')
                                  for field in self.FIELDS}
                                 if sample['burst_valid'] else None)
            sample['done_status'] = self.value('done_status') if sample['done_valid'] else None
            return sample

        sample = await tick(self.dut, snapshot)
        # Only the explicit queued-read cancel/error contract may withdraw an
        # unaccepted offer. Ordinary backpressure never changes its payload.
        queued = self.active is not None and not self.active.write and self.read_slots == 4
        stop_issue = queued and (sample['cancel'] or (
            sample['complete_valid'] and sample['complete_ready'] and sample['complete_status']))
        for channel, payload in (('burst', sample['payload']), ('done', sample['done_status'])):
            valid, ready = sample[f'{channel}_valid'], sample[f'{channel}_ready']
            if channel == 'burst' and stop_issue:
                self.held.pop(channel, None)
            if channel in self.held:
                assert valid and payload == self.held[channel], f'{channel} changed while stalled'
            if valid and not ready:
                self.held[channel] = payload
                COVERAGE[f'stalled_{channel}_cycles'] += 1
            else:
                self.held.pop(channel, None)

        was_outstanding = list(self.outstanding)
        if self.active is not None:
            assert sample['busy'] and not sample['req_ready'], 'Active request was not exclusive'
        if sample['busy']:
            assert not sample['req_ready']
        if was_outstanding:
            if not queued:
                assert not sample['burst_valid'], 'Serial operation offered another burst before completion'
            if not sample['complete_valid']:
                COVERAGE['delayed_completion_cycles'] += 1
        else:
            assert not sample['complete_ready'], 'Accepted completion before a prior burst handshake'

        if sample['done_valid']:
            assert self.active is not None and self.result_status is not None, 'Premature DONE'
            assert sample['done_status'] == self.result_status
            assert not was_outstanding and not sample['burst_valid']
            if self.result_status == 0:
                assert len(self.accepted) == len(self.expected), 'DONE omitted row words'
            if sample['done_ready']:
                self.active = None

        if sample['req_valid'] and sample['req_ready']:
            assert self.active is None
            self.active = Descriptor(**{name: sample[f'req_{name}']
                                        for name in ('write', 'base', 'stride', 'rows', 'row_bytes')})
            plan = expected_bursts(self.active)
            self.expected, self.accepted = plan or [], []
            self.outstanding.clear()
            self.result_status = 3 if plan is None else None
            COVERAGE['invalid_descriptors' if plan is None else 'valid_descriptors'] += 1
            if plan is not None:
                COVERAGE['write_operations' if self.active.write else 'read_operations'] += 1
                cover_value('row_counts', self.active.rows)
                cover_value('row_bytes', self.active.row_bytes)

        if stop_issue and self.result_status is None:
            self.result_status = sample['cancel_status'] if sample['cancel'] else sample['complete_status']
            assert self.result_status != 0
        if sample['complete_valid'] and sample['complete_ready']:
            assert was_outstanding, 'Same-edge first burst/completion was accepted'
            completed = self.outstanding.popleft()
            if sample['complete_status']:
                if self.result_status is None:
                    self.result_status = sample['complete_status']
                code = str(sample['complete_status'])
                COVERAGE['completion_errors'][code] = COVERAGE['completion_errors'].get(code, 0) + 1
            elif completed['last'] and self.result_status is None:
                self.result_status = 0

        if sample['burst_valid']:
            assert self.active is not None and self.result_status is None, 'Burst after error/invalid request'
            assert len(self.accepted) < len(self.expected), 'Extra burst'
            payload = sample['payload']
            assert payload == self.expected[len(self.accepted)], (
                f'{self.active}: burst {len(self.accepted)}: '
                f'{payload} != {self.expected[len(self.accepted)]}')
            assert 1 <= payload['beats'] <= 16 and payload['addr'] % 8 == 0
            assert payload['addr'] // 4096 == (payload['addr'] + 8 * payload['beats'] - 1) // 4096
            if sample['burst_ready']:
                limit = self.read_slots if queued else 1
                assert len(was_outstanding) < limit, 'Burst exceeded reserved ownership credits'
                self.accepted.append(payload)
                self.outstanding.append(payload)
                COVERAGE['burst_count'] += 1
                cover_value('burst_lengths', payload['beats'])
                cover_value('final_strobes', payload['final_strb'])

        if (sample['burst_valid'] and sample['burst_ready'] and
                sample['complete_valid'] and sample['complete_ready']):
            COVERAGE['same_edge_issue_retire'] += 1
        cover_value('outstanding_counts', len(self.outstanding))
        return sample

    async def wait_valid(self, name, limit=16):
        for _ in range(limit):
            if self.value(name):
                return
            await self.step()
        raise AssertionError(f'Timeout waiting for {name}')

    async def issue(self, desc):
        self.put(req_valid=1, done_ready=0, burst_ready=0,
                 complete_valid=0, complete_status=0, cancel=0, cancel_status=0, **desc.signals())
        for _ in range(8):
            sample = await self.step()
            if sample['req_valid'] and sample['req_ready']:
                break
        else:
            raise AssertionError('Request was not accepted')
        # Every descriptor pin changes, and VALID remains high during work.
        # Any combinational dependence or busy request acceptance is observable.
        self.put(req_valid=1, **Descriptor(1 - desc.write, desc.base ^ 0xffffffff,
                                        desc.stride ^ 0xffffffff, desc.rows ^ 63,
                                        desc.row_bytes ^ 511).signals())
        COVERAGE['snapshot_checks'] += 1

    async def finish_done(self, status, hold=3):
        await self.wait_valid('done_valid')
        for _ in range(hold):
            await self.step(burst_ready=1, complete_valid=0, done_ready=0)
        sample = await self.step(done_ready=1)
        assert sample['done_valid'] and sample['done_status'] == status
        self.put(req_valid=0, done_ready=0, complete_valid=0)
        await self.step()
        assert not self.value('busy') and self.value('req_ready')
        assert not self.value('done_valid') and not self.value('burst_valid')

    async def run(self, desc, rng, error_at=None, error_status=7):
        plan = expected_bursts(desc)
        await self.issue(desc)
        if plan is None:
            await self.finish_done(3)
            return []
        for index, expected in enumerate(plan):
            self.put(burst_ready=0)
            await self.wait_valid('burst_valid')
            for _ in range(rng.randrange(1, 4)):
                await self.step()
            sample = await self.step(burst_ready=1)
            assert sample['burst_valid'] and sample['payload'] == expected
            self.put(burst_ready=0)
            for _ in range(rng.randrange(1, 5)):
                await self.step(complete_valid=0)
            status = error_status if index == error_at else 0
            sample = await self.step(complete_valid=1, complete_status=status)
            assert sample['complete_ready'], 'Completion was not acknowledged'
            self.put(complete_valid=0, complete_status=status ^ 0xffff)
            if index == error_at:
                break
        accepted = list(self.accepted)
        await self.finish_done(error_status if error_at is not None else 0)
        return accepted


@cocotb.test()
async def directed_row_boundaries_and_snapshots(dut):
    h, rng = Harness(dut), random.Random(0xD001)
    await h.reset()
    cases = [
        ('page_last_word', Descriptor(0, 0xff8, 256, 1, 256)),
        ('page_last_eight_words', Descriptor(1, 0xfc0, 256, 2, 256)),
        ('maximum_tile', Descriptor(0, 0, 256, 32, 256)),
        ('maximum_write_tile', Descriptor(1, 0x18000, 264, 32, 256)),
        ('window_last_word_read', Descriptor(0, DDR_BYTES - 8, 8, 1, 1)),
        ('window_last_word_write', Descriptor(1, DDR_BYTES - 8, 8, 1, 4)),
        ('window_exact_end', Descriptor(1, DDR_BYTES - 512, 256, 2, 256)),
        ('single_row_ignores_next_stride', Descriptor(0, 0x100, 0xfffffff8, 1, 255)),
    ]
    for row_bytes in (1, 7, 8, 9, 63, 64, 65, 120, 127, 128, 129, 248, 255, 256):
        cases.append((f'read_bytes_{row_bytes}', Descriptor(0, 0x1ff8, 264, 3, row_bytes)))
    for columns in (1, 2, 3, 15, 16, 17, 31, 32, 33, 63, 64):
        cases.append((f'write_columns_{columns}', Descriptor(1, 0x3ff8, 264, 3, columns * 4)))
    for name, desc in cases:
        assert expected_bursts(desc) is not None
        accepted = await h.run(desc, rng)
        if name == 'page_last_word':
            assert [burst['beats'] for burst in accepted] == [1, 16, 15]
        cover_value('directed_cases', name)
    save_coverage()


@cocotb.test()
async def seeded_valid_and_invalid_descriptors(dut):
    h, rng = Harness(dut), random.Random(0xD002)
    await h.reset()
    for _ in range(200):
        write = rng.randrange(2)
        row_bytes = rng.randrange(1, 65) * 4 if write else rng.randrange(1, 257)
        rows = rng.choice((1, 2, 3, 7, 16, 31, 32, rng.randrange(1, 33)))
        storage = len(range(0, row_bytes, 8)) * 8
        stride = storage + 8 * rng.randrange(0, 129)
        footprint = (rows - 1) * stride + storage
        highest_base = (DDR_BYTES - footprint) // 8 * 8
        base = rng.choice((0, 0xff8, 0xfc0, 0x1000, highest_base,
                           rng.randrange(0, highest_base // 8 + 1) * 8))
        desc = Descriptor(write, base, stride, rows, row_bytes)
        assert expected_bursts(desc) is not None
        await h.run(desc, rng)
        COVERAGE['random_valid_descriptors'] += 1

    good = Descriptor(0, 0x1000, 256, 2, 256)
    bad = [
        replace(good, rows=0), replace(good, rows=33), replace(good, rows=63),
        replace(good, row_bytes=0), replace(good, row_bytes=257), replace(good, row_bytes=511),
        replace(good, base=1), replace(good, base=0xffffffff),
        replace(good, stride=257), replace(good, stride=0), replace(good, stride=248),
        replace(good, base=DDR_BYTES), replace(good, base=DDR_BYTES - 8, row_bytes=9, rows=1),
        replace(good, base=DDR_BYTES - 512 + 8),
        replace(good, base=0xfffffff8, stride=8, rows=2, row_bytes=8),
        replace(good, base=8, stride=0xfffffff8, rows=32, row_bytes=8),
        # The six-bit rows-1 intermediate is 63; the rounded footprint reaches
        # 2^38. Reject the original descriptor without issuing any burst.
        Descriptor(0, 0xfffffff8, 0xfffffff8, 0, 511),
        replace(good, stride=0x08000000, rows=2),
        replace(good, write=1, row_bytes=1), replace(good, write=1, row_bytes=6),
        replace(good, write=1, row_bytes=255),
    ]
    for desc in bad:
        assert expected_bursts(desc) is None
        await h.run(desc, rng)
    for _ in range(100):
        kind = rng.randrange(8)
        row_bytes = rng.randrange(1, 257)
        valid = Descriptor(0, rng.randrange(0, 1024) * 8, 256,
                           rng.randrange(1, 33), row_bytes)
        mutations = (
            replace(valid, rows=rng.choice((0, *range(33, 64)))),
            replace(valid, row_bytes=rng.choice((0, *range(257, 512)))),
            replace(valid, base=valid.base + rng.randrange(1, 8)),
            replace(valid, stride=rng.randrange(0, 32) * 8 + rng.randrange(1, 8)),
            replace(valid, stride=0),
            replace(valid, base=DDR_BYTES + rng.randrange(0, 1024) * 8),
            replace(valid, rows=rng.randrange(2, 33), stride=0xfffffff8),
            replace(valid, write=1, row_bytes=rng.randrange(0, 64) * 4 + rng.randrange(1, 4)),
        )
        desc = mutations[kind]
        assert expected_bursts(desc) is None
        await h.run(desc, rng)
        COVERAGE['random_invalid_descriptors'] += 1
    save_coverage()


@cocotb.test()
async def completion_faults_and_offer_chronology(dut):
    h, rng = Harness(dut), random.Random(0xD003)
    await h.reset()
    for write in (0, 1):
        desc = Descriptor(write, 0xff8, 264, 3, 252)
        count = len(expected_bursts(desc))
        for position, index in (('first', 0), ('middle', count // 2), ('final', count - 1)):
            for code in (7, 8):
                accepted = await h.run(desc, rng, error_at=index, error_status=code)
                assert len(accepted) == index + 1
                cover_value('error_positions', f'{position}:{code}:{write}')
                # Error completion does not permanently poison the planner.
                await h.run(Descriptor(write, 0x180, 8, 1, 8), rng)

    # READY must not acknowledge a completion before its burst handshake,
    # including the edge at which the burst itself is finally accepted.
    await h.issue(Descriptor(0, 0x200, 8, 1, 8))
    await h.wait_valid('burst_valid')
    for _ in range(4):
        sample = await h.step(complete_valid=1, complete_status=0, burst_ready=0)
        assert not sample['complete_ready']
    sample = await h.step(burst_ready=1)
    assert sample['burst_valid'] and not sample['complete_ready']
    sample = await h.step()
    assert sample['complete_ready']
    h.put(complete_valid=0)
    await h.finish_done(0)
    cover_value('directed_cases', 'completion_waits_for_prior_burst_handshake')
    save_coverage()


@cocotb.test()
async def local_reset_states(dut):
    h, rng = Harness(dut), random.Random(0xD004)
    for state in ('idle', 'multiply', 'footprint', 'validate', 'offer', 'wait', 'done'):
        await h.reset()
        if state != 'idle':
            await h.issue(Descriptor(1, 0xff8, 256, 2, 256))
            if state in ('multiply', 'footprint', 'validate'):
                # Request acceptance enters MULTIPLY; each following edge
                # advances the registered footprint before validation emits
                # any command. Reset each stage with changed live inputs.
                for _ in range(('multiply', 'footprint', 'validate').index(state)):
                    await h.step()
                assert h.value('busy') and not h.value('req_ready')
                assert not h.value('burst_valid') and not h.value('done_valid')
                assert not h.value('complete_ready')
            else:
                await h.wait_valid('burst_valid')
                if state in ('wait', 'done'):
                    await h.step(burst_ready=1)
                    if state == 'done':
                        # A local error ACK reaches a held completion without
                        # requiring the remaining bursts to execute.
                        await h.step(complete_valid=1, complete_status=7)
                        h.put(complete_valid=0)
                        await h.wait_valid('done_valid')
        await h.reset()
        await h.run(Descriptor(0, 0x4000, 16, 2, 9), rng)
        cover_value('reset_states', state)
    save_coverage()


@cocotb.test()
async def queued_read_issue_retire_and_cancellation(dut):
    h = Harness(dut)
    await h.reset()
    if h.read_slots == 1:
        save_coverage()
        return
    desc = Descriptor(0, 0xff8, 264, 8, 256)
    plan = expected_bursts(desc)
    await h.issue(desc)
    await h.wait_valid('burst_valid')
    for _ in range(4):
        sample = await h.step(burst_ready=1)
        assert sample['burst_valid']
    assert len(h.outstanding) == 4 and len(h.accepted) == 4
    for _ in range(11):
        sample = await h.step()
        assert not sample['burst_valid'] and not sample['done_valid']

    # Return one credit, then retire the next command on the exact edge a new
    # burst is accepted. The completion always refers to the old FIFO head.
    sample = await h.step(burst_ready=0, complete_valid=1, complete_status=0)
    assert sample['complete_ready']
    sample = await h.step(burst_ready=1)
    assert sample['burst_valid'] and sample['complete_ready']
    assert len(h.outstanding) == 3 and h.accepted == plan[:5]
    rng = random.Random(0xD005)
    for _ in range(2000):
        if h.value('done_valid'):
            break
        await h.step(burst_ready=rng.randrange(3) != 0,
                     complete_valid=bool(h.outstanding) and rng.randrange(3) != 0,
                     complete_status=0)
    else:
        raise AssertionError('Queued issue/retire did not finish')
    h.put(complete_valid=0)
    assert h.accepted == plan and not h.outstanding
    await h.finish_done(0, hold=17)
    cover_value('queued_scenarios', 'four_credits_row_page_metadata_same_edge_retire_issue')

    # Errors stop issue immediately, but all commands accepted before that
    # error still own a completion obligation. A held unaccepted offer can go.
    for error_status in (7, 8):
        await h.issue(desc)
        await h.wait_valid('burst_valid')
        for _ in range(4):
            await h.step(burst_ready=1)
        sample = await h.step(complete_valid=1, complete_status=error_status)
        assert sample['complete_ready'] and not sample['burst_valid']
        h.put(complete_valid=0)
        for _ in range(5):
            await h.step()
            assert not h.value('done_valid')
        while h.outstanding:
            sample = await h.step(complete_valid=1, complete_status=0)
            assert sample['complete_ready'] and not sample['burst_valid']
        h.put(complete_valid=0)
        assert len(h.accepted) == 4
        await h.finish_done(error_status)
    cover_value('queued_scenarios', 'error_stops_issue_then_drains_all_four')

    for accepted_count in (0, 2, 4):
        await h.issue(desc)
        await h.wait_valid('burst_valid')
        for _ in range(accepted_count):
            await h.step(burst_ready=1)
        for _ in range(4):
            await h.step(burst_ready=0)
        sample = await h.step(cancel=1, cancel_status=9, burst_ready=1)
        assert not sample['burst_valid'], 'Canceled an offer only after accepting it'
        h.put(cancel=0)
        while h.outstanding:
            for _ in range(3):
                await h.step(complete_valid=0)
                assert not h.value('done_valid')
            # First cancellation status remains sticky despite later errors.
            await h.step(complete_valid=1, complete_status=8)
        h.put(complete_valid=0)
        assert len(h.accepted) == accepted_count
        await h.finish_done(9)
        cover_value('queued_scenarios', f'cancel_with_{accepted_count}_accepted')

    # Reset only the local responder epoch together with the local planner.
    # This does not model resetting AXI or the DDR controller mid-transaction.
    await h.issue(desc)
    await h.wait_valid('burst_valid')
    for _ in range(4):
        await h.step(burst_ready=1)
    await h.step(burst_ready=0, complete_valid=1, complete_status=0)
    await h.step(complete_valid=0)
    assert len(h.outstanding) == 3 and h.value('burst_valid')
    await h.reset()
    await h.run(Descriptor(0, DDR_BYTES-1024, 256, 4, 256), rng)
    cover_value('reset_states', 'queued_read_mixed_owned_and_offered')
    save_coverage()
