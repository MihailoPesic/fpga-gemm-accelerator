"""Read-only verification of a completed matched-overlap UART transcript."""
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import struct
import zlib


def verify_uart(path, expected_build_id, expected_runs):
    """Bind final wire traffic to saved artifacts; never access a serial device.

    This is the private 60-job, P8/T32/READ4, 64x64x256 matched plan. The
    collector separately validates the mathematical oracle and guards. UART
    evidence cannot independently reveal AXI timing or DDR electrical behavior.
    """
    path = Path(path).resolve()
    area, root = path.parent, Path(__file__).resolve().parents[1]

    def require(condition, message):
        if not condition:
            raise RuntimeError('UART transcript: ' + message)

    def sha(file):
        return hashlib.sha256(file.read_bytes()).hexdigest()

    report = json.loads((area / 'results.json').read_text(encoding='utf-8'))
    require(report.get('state') == 'PASS' and report.get('passed') is True,
            'requires a completed PASS benchmark, not a running transcript')
    require(report.get('runs') == expected_runs and len(expected_runs) == 60,
            'expected runs differ from the completed result record')
    identity = report['identity']
    require(identity['build_id'] == expected_build_id and identity['version'] == 0x200
            and (identity['p'], identity['t'], identity['kmax'], identity['core_hz'])
            == (8, 32, 256, 100000000), 'unexpected image identity')
    require(report['shape'] == {'m': 64, 'n': 64, 'k': 256}, 'unexpected shape')
    require(report['retries_allowed'] == 0 and report['transport']['retries'] == 0
            and report['transport']['rejected_frames'] == 0
            and report['transport']['poisoned'] is False, 'transport record is not clean')
    for index, run in enumerate(expected_runs):
        modes = (0, 1) if index // 2 % 2 == 0 else (1, 0)
        require(run['passed'] is True and (run['job_id'], run['pair'], run['position'], run['mode'])
                == (index + 1, index // 2, index % 2, modes[index % 2]), 'wrong matched order')
    config = {'m': 0x18, 'n': 0x1c, 'k': 0x20, 'a_base': 0x24, 'bt_base': 0x28,
              'c_base': 0x2c, 'a_stride': 0x30, 'bt_stride': 0x34,
              'c_stride': 0x38, 'mode': 0x3c, 'watchdog': 0x4c}
    config_addresses = list(config.values())
    counters = {'job_cycles': 0x80, 'compute_cycles': 0x88, 'read_beats': 0x90,
                'write_beats': 0x98, 'write_valid_bytes': 0xa0, 'input_wait_cycles': 0xa8,
                'read_stall_cycles': 0xb0, 'write_stall_cycles': 0xb8}
    counter_words = [address + offset for address in counters.values() for offset in (0, 4)]
    descriptor = report['descriptors']['0']
    require((descriptor['m'], descriptor['n'], descriptor['k'], descriptor['a_stride'],
             descriptor['bt_stride'], descriptor['c_stride']) == (64, 64, 256, 320, 320, 320),
            'unexpected resident layout')
    require(descriptor['mode'] == 0 and report['descriptors']['1'] == dict(descriptor, mode=1),
            'matched descriptors differ beyond MODE')
    images = {image['name']: image for image in report['packed_images']}
    require(set(images) == {'a', 'bt', 'c'}, 'missing guarded allocations')
    bound_files = {}

    def read_artifact(file, size=None, digest=None):
        raw = file.read_bytes()
        require(size is None or len(raw) == size, 'artifact length differs: ' + str(file))
        actual_hash = hashlib.sha256(raw).hexdigest()
        require(digest is None or actual_hash == digest, 'artifact hash differs: ' + str(file))
        bound_files[file.relative_to(area).as_posix()] = actual_hash
        return raw

    def chunks(address, raw):
        require(address % 8 == 0 and len(raw) > 0 and len(raw) % 8 == 0, 'unaligned memory operation')
        offset = 0
        while offset < len(raw):
            length = min(240, len(raw) - offset, 4096 - (address + offset) % 4096)
            yield address + offset, raw[offset:offset + length]
            offset += length

    memory_stats = [dict(job_id=run['job_id'], c_prepare_bytes=0,
                         useful_output_read_bytes=0, allocation_read_bytes=0,
                         memory_write_commands=0, memory_read_commands=0)
                    for run in expected_runs]

    def memory_plan():
        for name in ('a', 'bt'):
            image = images[name]
            raw = read_artifact(area / f'initial_{name}_guarded.bin', image['bytes'], image['sha256_bytes'])
            for address, data in chunks(image['address'], raw):
                yield 5, address, data, 'initial_upload', None
        for index, run in enumerate(expected_runs):
            folder = area / run['directory']
            require(folder.resolve().is_relative_to(area), 'job artifact escapes output directory')
            raw = read_artifact(folder / 'c_before.bin', images['c']['bytes'], run['c_before_sha256_bytes'])
            for address, data in chunks(images['c']['address'], raw):
                yield 5, address, data, 'c_prepare', index
            output_file = folder / 'output.json'
            output = json.loads(read_artifact(output_file).decode('utf-8'))
            require(len(output) == 64 and all(len(row) == 64 for row in output), 'wrong output artifact shape')
            for row, values in enumerate(output):
                raw = struct.pack('<64i', *values)
                for address, data in chunks(descriptor['c_base'] + row * descriptor['c_stride'], raw):
                    yield 4, address, data, 'useful_output_read', index
            for name in ('a', 'bt', 'c'):
                image = images[name]
                raw = read_artifact(folder / f'{name}_after.bin', image['bytes'])
                for address, data in chunks(image['address'], raw):
                    yield 4, address, data, 'allocation_read', index

    memory = iter(memory_plan())
    memory_commands, register_reads, register_writes = Counter(), Counter(), Counter()
    initial_upload_bytes = 0
    pending, active, job_id_written = None, None, None
    config_written, config_read = [], []
    configured = {}
    starts, completions, ping_success, ping_conflict = 0, 0, 0, 0
    first_nonce, previous_sequence = None, None
    packet_counts = Counter()
    byte_counts, empty_delimiters = Counter(), Counter()
    buffers = {'tx': bytearray(), 'rx': bytearray()}
    previous_elapsed, event_count, closed = -1, 0, False
    identity_reads = {}

    def decode(encoded):
        require(0 < len(encoded) <= 269, 'invalid encoded frame length')
        raw, offset = bytearray(), 0
        while offset < len(encoded):
            code = encoded[offset]
            require(code != 0, 'embedded COBS zero')
            offset += 1
            stop = offset + code - 1
            require(stop <= len(encoded), 'truncated COBS block')
            raw.extend(encoded[offset:stop])
            offset = stop
            if code < 255 and offset < len(encoded):
                raw.append(0)
        require(len(raw) >= 10, 'short decoded frame')
        version, opcode, sequence, length = struct.unpack_from('<BBHH', raw)
        require(version == 1 and length <= 256 and len(raw) == length + 10,
                'invalid wire version or payload length')
        require(zlib.crc32(raw[:-4]) == struct.unpack_from('<I', raw, len(raw) - 4)[0],
                'CRC32 mismatch')
        return opcode, sequence, bytes(raw[6:-4])

    def request(packet):
        nonlocal pending, previous_sequence, first_nonce
        opcode, sequence, payload = packet
        require(pending is None and opcode in (1, 2, 3, 4, 5), 'overlapping or unknown request')
        if previous_sequence is not None:
            require(sequence == (previous_sequence + 1) % 65536, 'request sequence repeated or skipped')
        previous_sequence = sequence
        pending = dict(opcode=opcode, sequence=sequence, payload=payload)
        if opcode == 1:
            require(starts == 0 and ping_success == 0 and len(payload) == 4, 'unexpected PING')
            require(first_nonce is None or first_nonce == payload, 'PING conflict recovery changed nonce')
            first_nonce = payload
        elif opcode == 2:
            require(len(payload) == 2, 'bad READ_REG payload')
            pending['address'] = struct.unpack('<H', payload)[0]
        elif opcode == 3:
            require(len(payload) == 6, 'bad WRITE_REG payload')
            pending['address'], pending['value'] = struct.unpack('<HI', payload)
        else:
            require(active is None and len(payload) >= 6, 'host memory command overlaps active job')
            address, length = struct.unpack_from('<IH', payload)
            require(8 <= length <= 240 and address % 8 == 0 and length % 8 == 0
                    and address + length <= 128 * 1024 * 1024, 'illegal memory packet geometry')
            require(len(payload) == 6 + (length if opcode == 5 else 0), 'bad memory payload length')
            wanted = next(memory, None)
            require(wanted is not None, 'unexpected extra memory command')
            op, expected_address, data, phase, index = wanted
            require((opcode, address, length) == (op, expected_address, len(data)),
                    f'memory command differs in {phase}, job index {index}')
            if opcode == 5:
                require(payload[6:] == data, f'MEM_WRITE bytes differ in {phase}, job index {index}')
            pending.update(memory_data=data, phase=phase, job_index=index, memory_length=length)

    def response(packet):
        nonlocal pending, active, starts, completions, ping_success, ping_conflict
        nonlocal job_id_written, initial_upload_bytes
        opcode, sequence, payload = packet
        require(pending is not None and (opcode, sequence)
                == (pending['opcode'] | 0x80, pending['sequence']), 'unmatched response')
        require(2 <= len(payload) <= 242, 'invalid response payload size')
        status = struct.unpack_from('<H', payload)[0]
        data, request_opcode = payload[2:], pending['opcode']
        if status:
            require(status == 6 and request_opcode == 1 and packet_counts['tx'] == 1
                    and ping_conflict == 0 and not data, 'non-OKAY execution response')
            ping_conflict += 1
        elif request_opcode == 1:
            require(len(data) == 12 and data[:4] == pending['payload']
                    and struct.unpack_from('<II', data, 4) == (0x314d474e, 0x200), 'PING identity differs')
            ping_success += 1
        elif request_opcode == 2:
            require(len(data) == 4, 'READ_REG response length differs')
            address, value = pending['address'], struct.unpack('<I', data)[0]
            register_reads[address] += 1
            identities = {0: 0x314d474e, 4: 0x200, 8: (256 << 16) | (32 << 8) | 8,
                          0x50: expected_build_id, 0x48: 100000000}
            if address in identities:
                require(value == identities[address], 'register identity differs')
                identity_reads[address] = value
            elif address in config_addresses:
                require(active is None and starts < 60 and config_written == config_addresses,
                        'configuration readback precedes full writes or overlaps job')
                require(len(config_read) < 11 and address == config_addresses[len(config_read)]
                        and value == configured[address],
                        'configuration snapshot readback differs')
                config_read.append(address)
            elif address == 0x0c:
                require(value & ~0x3f == 0 and value & 0x28 == 0 and value & 0x10,
                        'status has error, reset-required, lost calibration or reserved bits')
                require(not (value & 1 and value & 2) and not (value & 4 and value & 2),
                        'inconsistent READY/BUSY/DONE')
                if active is not None:
                    if value & 4:
                        require(value & 1, 'DONE without READY')
                        if active['done_status'] is None:
                            active['done_status'] = value
                        else:
                            require(value == active['done_status'], 'status changed while counters were frozen')
                        if len(active['words']) == 16:
                            active['final_status_seen'] = True
                    else:
                        require(value & 2, 'active job disappeared')
                else:
                    require(value & 1 and not value & 2, 'idle host access without READY')
            elif address == 0x44:
                require(starts > 0 and value == expected_runs[starts - 1]['job_id'], 'LAST_JOB_ID differs')
                if active is not None and len(active['words']) == 16:
                    require(active['done_status'] is not None and active['final_status_seen'],
                            'completion without final frozen-status recheck')
                    for name, low in counters.items():
                        got = active['words'][low] | (active['words'][low + 4] << 32)
                        require(got == expected_runs[active['index']][name], 'frozen ' + name + ' differs')
                    completions += 1
                    active = None
            elif address in counter_words:
                require(active is not None and active['done_status'] is not None, 'counter read before DONE')
                require(len(active['words']) < 16 and address == counter_words[len(active['words'])],
                        'counter read order or count differs')
                active['words'][address] = value
            else:
                require(False, f'unexpected register read 0x{address:04x}')
        elif request_opcode == 3:
            require(not data and active is None and starts < 60, 'write response data or active configuration change')
            address, value = pending['address'], pending['value']
            register_writes[address] += 1
            run = expected_runs[starts]
            desc = report['descriptors'][str(run['mode'])]
            if address in config_addresses:
                require(len(config_written) < 11 and address == config_addresses[len(config_written)],
                        'descriptor write order/count differs')
                name = next(name for name, offset in config.items() if offset == address)
                require(value == desc[name], 'descriptor write differs: ' + name)
                configured[address] = value
                config_written.append(address)
            elif address == 0x14:
                require(config_read == config_addresses and value == run['job_id'], 'JOB_ID or snapshot differs')
                job_id_written = value
            elif address == 0x10:
                require(value == 1 and job_id_written == run['job_id'] and config_read == config_addresses,
                        'invalid, repeated or unconfigured START')
                require(memory_stats[starts]['c_prepare_bytes'] == images['c']['bytes'],
                        'START before complete C sentinel preparation')
                active = dict(index=starts, done_status=None, words={}, final_status_seen=False)
                starts += 1
                config_written.clear()
                config_read.clear()
                job_id_written = None
            else:
                require(False, f'unexpected register write 0x{address:04x}')
        else:
            require(data == (pending['memory_data'] if request_opcode == 4 else b''),
                    'memory response differs from saved artifact')
            memory_commands[request_opcode] += 1
            index, length, phase = pending['job_index'], pending['memory_length'], pending['phase']
            if index is None:
                initial_upload_bytes += length
            else:
                memory_stats[index][phase + '_bytes'] += length
                memory_stats[index]['memory_write_commands' if request_opcode == 5 else 'memory_read_commands'] += 1
        pending = None

    with path.open(encoding='utf-8') as stream:
        for event_count, line in enumerate(stream, 1):
            event = json.loads(line)
            require(not closed, 'events occur after serial close')
            elapsed = event['elapsed_ns']
            require(type(elapsed) is int and elapsed >= previous_elapsed, 'nonmonotonic event clock')
            previous_elapsed = elapsed
            require(datetime.fromisoformat(event['utc']).tzinfo is not None, 'UTC event lacks timezone')
            require('error' not in event, 'recorded UART I/O error')
            if event.get('event') == 'serial_closed':
                require(pending is None and not any(buffers.values()), 'close with incomplete transaction/frame')
                closed = True
                continue
            direction = event.get('direction')
            require(direction in buffers and re.fullmatch(r'(?:[0-9a-f]{2})+', event.get('hex', '')),
                    'invalid byte event')
            raw = bytes.fromhex(event['hex'])
            if direction == 'tx':
                require(type(event['accepted_bytes']) is int and event['accepted_bytes'] == len(raw)
                        and event['requested_bytes'] >= len(raw), 'TX accepted-byte count differs')
            byte_counts[direction] += len(raw)
            for byte in raw:
                if byte:
                    buffers[direction].append(byte)
                    require(len(buffers[direction]) <= 269, 'oversized unfinished frame')
                elif not buffers[direction]:
                    empty_delimiters[direction] += 1
                else:
                    packet = decode(buffers[direction])
                    buffers[direction].clear()
                    packet_counts[direction] += 1
                    (request if direction == 'tx' else response)(packet)
    require(closed and pending is None and active is None and starts == completions == 60,
            'missing close, complete transactions or all 60 jobs')
    require(ping_success == 1 and set(identity_reads) == {0, 4, 8, 0x48, 0x50}, 'missing identity checks')
    require(next(memory, None) is None, 'missing expected memory commands')
    require(packet_counts['tx'] == packet_counts['rx'], 'unpaired frame counts')
    require(all(register_writes[address] == 60 and register_reads[address] == 60
                for address in config_addresses), 'descriptor snapshot totals differ')
    require(register_writes[0x14] == register_writes[0x10] == 60, 'JOB_ID/START write totals differ')
    require(all(register_reads[address] == 60 for address in counter_words), 'counter word totals differ')
    require(all(item['c_prepare_bytes'] == images['c']['bytes']
                and item['useful_output_read_bytes'] == 64 * 64 * 4
                and item['allocation_read_bytes'] == sum(image['bytes'] for image in images.values())
                for item in memory_stats), 'per-job memory totals differ')
    sources = [Path(__file__).resolve(), root / 'build/bench_overlap_matched.py',
               root / 'host/preview/protocol.py', root / 'host/gemm/client.py']
    return dict(schema_version=1, result='PASS', scope='Completed 60-job matched UART/artifact consistency only; '
                'arithmetic/guards require the collector oracle; no independent AXI timing or electrical proof',
                uart_sha256_bytes=sha(path), results_sha256_bytes=sha(area / 'results.json'),
                expected_build_id=expected_build_id, events=event_count, byte_counts=dict(byte_counts),
                frame_counts=dict(packet_counts), empty_delimiters=dict(empty_delimiters),
                starts=starts, completions=completions, ping_conflicts=ping_conflict,
                memory_commands={str(k): v for k, v in memory_commands.items()},
                initial_upload_bytes=initial_upload_bytes, per_job_memory=memory_stats,
                register_reads={hex(k): v for k, v in register_reads.items()},
                register_writes={hex(k): v for k, v in register_writes.items()},
                artifact_sha256_bytes=bound_files,
                source_sha256_bytes={file.relative_to(root).as_posix(): sha(file) for file in sources})
