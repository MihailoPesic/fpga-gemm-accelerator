"""Decode and independently check FIFO and reduced-scheduler SAT witnesses."""
import hashlib
import json


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def port_widths(kind, configuration):
    if kind == 'queue':
        bits = ('clk rst plan_valid cmd_ready bank_ready terminal_ready data_choice done_choice stop_request '
                'plan_ready cmd_valid data_valid data_ready done_valid done_ready bank_valid bank_bt bank_buf '
                'terminal_valid load_pending completion_pending stop cover_full cover_wrap cover_stalls cover_stop_drain')
        widths = dict.fromkeys(bits.split(), 1)
        widths.update(plan_row=5,plan_word=5,plan_addr=32,data_value=64,cmd_addr=32,cmd_beats=5,cmd_tag=16,
            bank_row=configuration.bit_length()-1,bank_word=5,bank_data=64,terminal_status=16,fault_code=16,
            owned=3,head=2,tail=2,received=5,delivered=5,expected_owned=3,expected_head=2,expected_tail=2)
        widths.update({name+str(i):5 for name in ('row','word','beats') for i in range(4)})
    else:
        bits = ('clk rst job_request load_ready_choice store_ready_choice core_ready_choice load_finish store_finish '
                'core_finish load_failure store_failure mem_fatal compute_error job_ready busy done fatal load_valid '
                'load_ready load_bt load_buf store_valid store_ready store_buf core_start core_complete core_busy '
                'core_input core_output load_pending store_pending core_pending load_end store_end core_done '
                'cover_success cover_overlap cover_held cover_fault_drain')
        widths = dict.fromkeys(bits.split(), 1)
        widths.update(fatal_code=16,load_base=32,load_stride=32,load_rows=6,load_bytes=9,store_base=32,
            store_stride=32,store_rows=6,store_bytes=9,core_m=4,core_n=4,core_k=9,
            input0=2,input1=2,output0=2,output1=2,load_state=3,store_state=2,core_state=2,job_state=2,
            admitted=16,computed=16,retired=16,total=16,input_tag0=15,input_tag1=15,output_tag0=15,output_tag1=15)
    return widths


def decode(vcd, model, bound, expected_widths):
    names, widths, state, frames = {}, {}, {}, {}
    tick = None
    for line in vcd.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line.startswith('$var '):
            parts = line.split()
            name = parts[4].lstrip('\\')
            if name not in expected_widths or len(parts) != 6:
                continue  # Yosys also dumps initial-only internal registers.
            require(name not in widths, f'{vcd.name}: duplicate signal {name}')
            names[parts[3]], widths[name] = name, int(parts[2])
        elif line.startswith('#'):
            if tick is not None:
                frames[tick] = dict(state)
            tick = int(line[1:])
        elif line.startswith('b'):
            bits, key = line[1:].split()
            if key not in names:
                continue
            require(set(bits) <= {'0', '1'}, f'{vcd.name}: undefined bus')
            state[names[key]] = int(bits, 2)
        elif line and line[0] in '01xz':
            if line[1:] not in names:
                continue
            require(line[0] in '01', f'{vcd.name}: undefined bit')
            state[names[line[1:]]] = int(line[0])
    if tick is not None:
        frames[tick] = dict(state)
    require(0 in frames and 1 not in frames and set(range(2, bound + 1)) <= frames.keys(),
            f'{vcd.name}: wrong SAT timeframe map')
    frames[1] = frames[0]
    signals = json.loads(model.read_text(encoding='utf-8'))['signal']
    by_name = {signal['name']: signal for signal in signals}
    require(len(by_name) == len(signals), f'{model.name}: duplicate signals')
    require(widths == expected_widths, f'{vcd.name}: public port set/width mismatch')
    for name, width in widths.items():
        require(name in by_name, f'{model.name}: missing {name}')
        signal = by_name[name]
        wave, data = signal['wave'], signal.get('data', [])
        require(len(wave) == bound + 1, f'{model.name}: wrong bound')
        index, value = 0, None
        for step, token in enumerate(wave):
            if token in '01':
                value = int(token)
            elif token == '4' and step == 0:
                if data:
                    require(data[0] == '', f'{model.name}: wrong initial marker')
                    index = 1
            elif token == '=':
                require(index < len(data) and data[index] and set(data[index]) <= {'0', '1'},
                        f'{model.name}: undefined data {name}')
                value, index = int(data[index], 2), index + 1
            else:
                require(token == '.', f'{model.name}: unknown waveform token')
            if step:
                require(frames[step].get(name) == value and isinstance(value, int) and 0 <= value < 2**width,
                        f'{model.name}: JSON/VCD mismatch {name} at {step}')
        require(index == len(data), f'{model.name}: unused waveform values')
    return [dict(step=step, **frames[step]) for step in range(1, bound + 1)]


def queue_witness(frames, target, t):
    queue, pushes, pops, previous = [], 0, 0, None
    received, delivered, terminal = False, False, False
    data, status = None, None
    bank_stalls, terminal_stalls, full, stop_edge, later_retire = [], [], [], None, False
    table = ['step stop owned cmd tag row word data/bank done/terminal pushes pops']
    for f in frames:
        where = f'queue timeframe {f["step"]}'
        if f['rst']:
            require(f['step'] == 1, where + ': reset mismatch')
            continue
        require(f['step'] != 1 and f['owned'] == len(queue) == f['expected_owned'], where + ': occupancy mismatch')
        require(f['head'] == pops % 4 and f['tail'] == pushes % 4, where + ': FIFO cursor mismatch')
        # The final sample observes the last committed edge. Its input offers
        # would commit outside the trace and its clocked assumptions have not
        # been sampled yet; do not count an unexecuted edge as FIFO traffic.
        execute = f['step'] < len(frames)
        issue = execute and f['cmd_valid'] and f['cmd_ready']
        bank_fire = execute and f['bank_valid'] and f['bank_ready']
        data_fire = execute and f['data_valid'] and f['data_ready']
        done_fire = execute and f['done_valid'] and f['done_ready']
        pop = execute and f['terminal_valid'] and f['terminal_ready']
        if f['owned'] == 4:
            full.append(f['step'])
        if execute and f['stop_request'] and not f['stop'] and queue and not pop:
            stop_edge = f['step']
        if f['bank_valid']:
            require(queue and received and not delivered and not terminal, where + ': unowned bank offer')
            require((f['bank_row'], f['bank_word']) == queue[0] and f['bank_data'] == data,
                    where + ': reordered/corrupt bank payload')
            require(f['bank_bt'] == f['bank_buf'] == 1, where + ': wrong saved destination')
            if not f['bank_ready']:
                bank_stalls.append(f['step'])
        if f['terminal_valid']:
            require(queue and terminal and not f['bank_valid'] and f['terminal_status'] == status,
                    where + ': premature or corrupt terminal')
            if not f['terminal_ready']:
                terminal_stalls.append(f['step'])
        if previous and previous['bank_valid'] and not previous['bank_ready']:
            fields = ('bank_valid', 'bank_bt', 'bank_buf', 'bank_row', 'bank_word', 'bank_data')
            require(tuple(f[k] for k in fields) == tuple(previous[k] for k in fields), where + ': bank stall instability')
        if previous and previous['terminal_valid'] and not previous['terminal_ready']:
            require(f['terminal_valid'] and f['terminal_status'] == previous['terminal_status'], where + ': terminal stall instability')
        if bank_fire:
            delivered = True
        if data_fire:
            require(queue and not received and not terminal, where + ': duplicate/unowned data')
            received, data = True, f['data_value']
        if done_fire:
            require(queue and received and (f['stop'] or delivered), where + ': premature upstream terminal')
            terminal, status = True, 7 if f['stop'] else 0
        if pop:
            require(queue and terminal, where + ': FIFO underflow')
            queue.pop(0)
            pops += 1
            received = delivered = terminal = False
            if stop_edge is not None and f['step'] > stop_edge:
                later_retire = True
        if issue:
            require(len(queue) < 4 and not f['stop'] and f['cmd_tag'] == pushes % 4 and f['cmd_beats'] == 1,
                    where + ': bad FIFO admission')
            require(f['plan_row'] < t and f['cmd_addr'] == f['plan_addr'], where + ': invalid planner payload')
            queue.append((f['plan_row'], f['plan_word']))
            pushes += 1
        table.append(f"{f['step']:>2} {f['stop']} {f['owned']} {int(issue)} {f['cmd_tag']} "
                     f"{f['bank_row']} {f['bank_word']} {int(data_fire)}/{int(bank_fire)} "
                     f"{int(done_fire)}/{int(pop)} {pushes} {pops}")
        previous = f
    require(any(f[target] for f in frames), 'Queue cover absent')
    checks = dict(cover_full=bool(full), cover_wrap=pushes >= 5 and pops >= 5,
                  cover_stalls=bool(bank_stalls and terminal_stalls and pops),
                  cover_stop_drain=stop_edge is not None and later_retire and not queue)
    require(checks[target], f'Queue witness does not demonstrate {target}')
    return dict(pushes=pushes, pops=pops, full_steps=full, bank_stall_steps=bank_stalls,
                terminal_stall_steps=terminal_stalls, stop_with_work_step=stop_edge,
                later_retire=later_retire, cycle_table='\n'.join(table))


def scheduler_witness(frames, target, mode):
    loads, starts, stores, store_completions = 0, 0, 0, 0
    load_owner = store_owner = core_owner = None
    ready_inputs, ready_outputs = set(), set()
    overlap, load_stalls, store_stalls, fault_edge, later_completion = [], [], [], None, False
    table = ['step fatal busy owners(load/core/store) admitted computed retired input0/1 output0/1 done']
    for f in frames:
        where = f'scheduler timeframe {f["step"]}'
        if f['rst']:
            require(f['step'] == 1, where + ': reset mismatch')
            continue
        require(f['step'] != 1, where + ': reset mismatch')
        if f['step'] == len(frames):
            # Final observation only, not another abstract engine transition.
            table.append(f"{f['step']:>2} {f['fatal']} {f['busy']} FINAL_OBSERVATION {f['done']}")
            continue
        load_fire, store_fire = f['load_valid'] and f['load_ready'], f['store_valid'] and f['store_ready']
        if f['load_valid'] and not f['load_ready']:
            load_stalls.append(f['step'])
        if f['store_valid'] and not f['store_ready']:
            store_stalls.append(f['step'])
        if f['load_pending'] and f['core_pending'] and f['load_buf'] != f['core_input']:
            overlap.append(f['step'])
        if fault_edge is None and (f['mem_fatal'] or f['compute_error'] or
                f['load_end'] and f['load_failure'] or f['store_end'] and f['store_failure']):
            if load_owner is not None or store_owner is not None or core_owner is not None:
                fault_edge = f['step']
        if f['load_end']:
            require(load_owner is not None, where + ': unowned load completion')
            if load_owner[1] == 1 and not f['load_failure']:
                ready_inputs.add(load_owner[0])
            load_owner = None
            if fault_edge is not None and f['step'] > fault_edge:
                later_completion = True
        if f['core_complete']:
            require(core_owner is not None, where + ': unowned compute completion')
            ready_inputs.discard(core_owner[0])
            ready_outputs.add(core_owner[1])
            core_owner = None
            if fault_edge is not None and f['step'] > fault_edge:
                later_completion = True
        if f['store_end']:
            require(store_owner is not None, where + ': unowned store completion')
            ready_outputs.discard(store_owner)
            store_owner = None
            store_completions += 1
            if fault_edge is not None and f['step'] > fault_edge:
                later_completion = True
        if load_fire:
            require(load_owner is None and f['load_bt'] == loads % 2, where + ': load ordering')
            require(core_owner is None or core_owner[0] != f['load_buf'], where + ': load/compute input conflict')
            load_owner = (f['load_buf'], f['load_bt'])
            loads += 1
        if f['core_start']:
            require(core_owner is None and f['core_input'] in ready_inputs and f['core_output'] not in ready_outputs,
                    where + ': compute used an unready/live buffer')
            require(load_owner is None or load_owner[0] != f['core_input'], where + ': compute/load conflict')
            require(store_owner is None or store_owner != f['core_output'], where + ': compute/store conflict')
            core_owner = (f['core_input'], f['core_output'])
            starts += 1
        if store_fire:
            require(store_owner is None and f['store_buf'] in ready_outputs, where + ': unready store')
            require(core_owner is None or core_owner[1] != f['store_buf'], where + ': store/compute conflict')
            store_owner = f['store_buf']
            stores += 1
        if mode == 0:
            require(sum(owner is not None for owner in (load_owner, core_owner, store_owner)) <= 1,
                    where + ': serial owners overlap')
        if f['done']:
            require(loads == 4 and starts == stores == store_completions == 2 and
                    load_owner is None and store_owner is None and core_owner is None,
                    where + ': premature success')
        table.append(f"{f['step']:>2} {f['fatal']} {f['busy']} {load_owner}/{core_owner}/{store_owner} "
                     f"{f['admitted']} {f['computed']} {f['retired']} {f['input0']}/{f['input1']} "
                     f"{f['output0']}/{f['output1']} {f['done']}")
    require(any(f[target] for f in frames), 'Scheduler cover absent')
    checks = dict(cover_success=any(f['done'] for f in frames) and loads == 4 and starts == stores == store_completions == 2,
                  cover_overlap=mode == 1 and bool(overlap),
                  cover_held=bool(load_stalls and store_stalls),
                  cover_fault_drain=fault_edge is not None and later_completion and
                      any(f['step'] > fault_edge and f['fatal'] and not f['busy'] and not f['done'] and
                          f['input0'] == f['input1'] == f['output0'] == f['output1'] == 0 for f in frames))
    require(checks[target], f'Scheduler witness does not demonstrate {target}')
    return dict(load_requests=loads, compute_starts=starts, store_requests=stores,
                store_completions=store_completions, overlap_steps=overlap, load_stall_steps=load_stalls,
                store_stall_steps=store_stalls, fault_with_work_step=fault_edge,
                later_completion=later_completion, cycle_table='\n'.join(table))


def review_witness(vcd, model, bound, kind, configuration, target):
    frames = decode(vcd, model, bound, port_widths(kind, configuration))
    reviewed = queue_witness(frames, target, configuration) if kind == 'queue' else scheduler_witness(frames, target, configuration)
    return dict(result='PASS', kind=kind, configuration=configuration, target=target,
                vcd=vcd.name, model=model.name, vcd_sha256_bytes=digest(vcd), model_sha256_bytes=digest(model), **reviewed)
