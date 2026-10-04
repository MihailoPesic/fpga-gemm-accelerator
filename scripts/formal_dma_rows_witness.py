"""Check public command handshakes in the row-planner SAT witnesses."""
import hashlib
import json

BOUND = 20
EXPECTED = (
    (0xff0, 2, 0, 0, 0, 0), (0x1000, 1, 0, 2, 1, 0),
    (0x1030, 3, 1, 0, 1, 0), (0x1070, 3, 2, 0, 1, 0), (0x10b0, 3, 3, 0, 1, 1),
)
WIDTHS = dict.fromkeys((
    'clk', 'rst', 'req_valid', 'req_write', 'req_ready', 'busy', 'burst_ready',
    'burst_valid', 'burst_write', 'burst_row_last', 'burst_last', 'complete_valid',
    'complete_ready', 'cancel', 'done_ready', 'done_valid', 'cover_read_done',
    'cover_write_done', 'cover_credit_four', 'cover_simultaneous', 'cover_cancelled_drain'), 1)
WIDTHS.update(complete_status=16, cancel_status=16, done_status=16, burst_addr=32,
              burst_beats=5, burst_row=5, burst_word=5, burst_final_strb=8, pending=4)
FIELDS = ('burst_addr', 'burst_beats', 'burst_row', 'burst_word', 'burst_row_last', 'burst_last')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decode(vcd, model):
    names, widths, state, frames = {}, {}, {}, {}
    tick = None
    for raw in vcd.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if line.startswith('$var '):
            parts = line.split()
            name = parts[4].lstrip('\\')
            names[parts[3]], widths[name] = name, int(parts[2])
        elif line.startswith('#'):
            if tick is not None:
                frames[tick] = dict(state)
            tick = int(line[1:])
        elif line and line[0] in '01xz':
            name = names.get(line[1:])
            if name:
                state[name] = int(line[0]) if line[0] in '01' else None
        elif line.startswith('b'):
            bits, key = line[1:].split()
            name = names.get(key)
            if name:
                state[name] = int(bits, 2) if set(bits) <= {'0', '1'} else None
    if tick is not None:
        frames[tick] = dict(state)
    require(1 not in frames and 0 in frames and set(range(2, BOUND + 1)) <= frames.keys(),
            f'{vcd.name}: unexpected SAT timestep mapping')
    # Yosys dumps SAT timeframe1 at VCD#0; #2 onward matches the timeframe number.
    frames[1] = frames[0]
    for name, width in WIDTHS.items():
        require(widths.get(name) == width, f'{vcd.name}: missing or wrong-width {name}')
        for step in range(1, BOUND + 1):
            value = frames[step].get(name)
            require(isinstance(value, int) and 0 <= value < 2**width,
                    f'{vcd.name}: undefined {name} at timeframe{step}')

    signals = json.loads(model.read_text(encoding='utf-8'))['signal']
    by_name = {signal['name']: signal for signal in signals}
    require(len(by_name) == len(signals), f'{model.name}: duplicate signal names')
    for name in WIDTHS:
        require(name in by_name, f'{model.name}: missing {name}')
        signal = by_name[name]
        wave, data = signal['wave'], signal.get('data', [])
        require(len(wave) == BOUND + 1 and wave[0] in '014=', f'{model.name}: wrong bound for {name}')
        index, value = 0, None
        for step, token in enumerate(wave):
            if token in '01':
                value = int(token)
            elif token == '4' and step == 0:
                if data:
                    require(data[0] == '', f'{model.name}: wrong initial data marker')
                    index = 1
            elif token == '=':
                require(index < len(data) and data[index] and set(data[index]) <= {'0', '1'},
                        f'{model.name}: invalid bus data for {name}')
                value, index = int(data[index], 2), index + 1
            else:
                require(token == '.', f'{model.name}: unexpected wave token {token}')
            if step:
                require(value == frames[step][name], f'{model.name}: VCD/JSON mismatch {name} at{step}')
        require(index == len(data), f'{model.name}: unconsumed bus data for {name}')
    return [dict(step=step, **frames[step]) for step in range(1, BOUND + 1)]


def review_witness(vcd, model, slots, target):
    frames = decode(vcd, model)
    pending, operation, previous = 0, None, None
    operations, table, credits, simultaneous, cancellations = [], [], [], [], []
    for frame in frames:
        step = frame['step']
        issue = bool(frame['burst_valid'] and frame['burst_ready'])
        complete = bool(frame['complete_valid'] and frame['complete_ready'])
        request = bool(frame['req_valid'] and frame['req_ready'])
        terminal = bool(frame['done_valid'] and frame['done_ready'])
        where = f'{vcd.name}: timeframe{step}'
        require(frame['rst'] == int(step == 1), where + ': reset assumption mismatch')
        table.append(f"{step:>2} {frame['rst']} {int(request)}/{frame['req_write']} "
                     f"{frame['burst_valid']}{frame['burst_ready']} {frame['burst_addr']:08x} "
                     f"{frame['burst_beats']:>2} {frame['pending']} "
                     f"{frame['complete_valid']}{frame['complete_ready']} "
                     f"{frame['done_valid']}{frame['done_ready']} {frame['done_status']:04x} "
                     f"{frame['cancel']} {frame['cancel_status']:04x}")
        if frame['rst']:
            pending, operation, previous = 0, None, None
            continue
        require(frame['pending'] == pending, where + ': outstanding count mismatch')
        require(not frame['complete_ready'] or pending > 0, where + ': completion without ownership')
        require(not frame['done_valid'] or pending == 0, where + ': premature DONE')
        payload = tuple(frame[name] for name in ('burst_write',) + FIELDS + ('burst_final_strb',))
        withdrawal = slots == 4 and not frame['burst_write'] and (
            frame['cancel'] or complete and frame['complete_status'] != 0)
        if previous and previous['burst_valid'] and not previous['burst_ready'] and not withdrawal:
            old = tuple(previous[name] for name in ('burst_write',) + FIELDS + ('burst_final_strb',))
            require(frame['burst_valid'] and payload == old, where + ': stalled offer changed')
        if previous and previous['done_valid'] and not previous['done_ready']:
            require(frame['done_valid'] and frame['done_status'] == previous['done_status'],
                    where + ': stalled DONE changed')
        if request:
            require(pending == 0 and not issue and not complete, where + ': conflicting request')
            operation = dict(start=step, write=frame['req_write'], bursts=[], completions=[],
                             offer_stalls=[], done_stalls=[], cancel_edges=[])
            operations.append(operation)
        if frame['busy'] and frame['cancel'] and operation:
            operation['cancel_edges'].append(step)
            if not operation['write'] and frame['cancel_status'] != 0 and pending - int(complete) > 0:
                cancellations.append(step)
        if frame['burst_valid']:
            require(operation is not None and len(operation['bursts']) < len(EXPECTED),
                    where + ': offer without an available descriptor word')
            fields = tuple(frame[name] for name in FIELDS)
            require(fields == EXPECTED[len(operation['bursts'])], where + ': wrong burst fields/order')
            require(frame['burst_write'] == operation['write'], where + ': wrong burst direction')
            strobe = 0x0f if operation['write'] and fields[4] else 0xff
            require(frame['burst_final_strb'] == strobe, where + ': wrong final strobe')
            if not frame['burst_ready']:
                operation['offer_stalls'].append(step)
        if frame['done_valid'] and not frame['done_ready'] and operation:
            operation['done_stalls'].append(step)
        if pending == 4:
            require(operation is not None and not operation['write'], where + ': write reached four credits')
            credits.append(step)
        if issue and complete:
            require(operation is not None and not operation['write'], where + ': simultaneous write issue/completion')
            simultaneous.append(step)
        if issue:
            operation['bursts'].append(dict(step=step, addr=frame['burst_addr'], beats=frame['burst_beats']))
        if complete:
            require(operation is not None and pending > 0, where + ': unmatched completion')
            operation['completions'].append(dict(step=step, status=frame['complete_status']))
        if terminal:
            require(operation is not None and len(operation['bursts']) == len(operation['completions']),
                    where + ': terminal with incomplete accepted traffic')
            operation.update(done=step, status=frame['done_status'])
        pending += int(issue) - int(complete)
        if operation:
            require(0 <= pending <= (1 if operation['write'] else slots), where + ': credit bound exceeded')
        previous = frame

    require(any(frame[target] == 1 for frame in frames), f'{vcd.name}: target is unreachable')
    if target in ('cover_read_done', 'cover_write_done'):
        direction = int(target == 'cover_write_done')
        require(any(op.get('done') and op['write'] == direction and op['status'] == 0
                    and len(op['bursts']) == 5 and len(op['completions']) == 5 and not op['cancel_edges']
                    and op['offer_stalls'] and op['done_stalls']
                    and all(item['status'] == 0 for item in op['completions']) for op in operations),
                f'{vcd.name}: no complete successful operation with both stalls')
    elif target == 'cover_credit_four':
        require(bool(credits), f'{vcd.name}: four-credit target lacks accepted traffic')
    elif target == 'cover_simultaneous':
        require(bool(simultaneous), f'{vcd.name}: no simultaneous issue/completion')
    elif target == 'cover_cancelled_drain':
        require(any(op.get('done') and not op['write'] and op['status'] != 0
                    and any(op['start'] <= edge < op['done']
                            and any(edge < item['step'] < op['done'] for item in op['completions'])
                            for edge in cancellations) for op in operations),
                f'{vcd.name}: no nonzero cancellation followed by later completion and DONE')
    else:
        raise RuntimeError(f'Unknown witness target: {target}')
    return dict(result='PASS', read_slots=slots, target=target, operations=operations,
                credit_four_steps=credits, simultaneous_steps=simultaneous,
                cancellation_with_pending_steps=cancellations, cycle_table='\n'.join(table),
                vcd=vcd.name, vcd_sha256_bytes=digest(vcd),
                model=model.name, model_sha256_bytes=digest(model))
