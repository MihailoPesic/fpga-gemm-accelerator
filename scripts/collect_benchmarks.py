"""Collect sealed P8 T8/T32 measurements and plot controlled reuse/overlap.

Requires T8 MODE0 (A), T32 MODE0 (B) and T32 MODE1 (C) from the public release
runner. B and C must use the same bitstream. No board access or vendor tools.
The default requires the complete 16-case grid; --allow-partial reports only
common completed cases and states the missing coverage explicitly.
"""
import argparse
import csv
from datetime import datetime, timezone
from importlib.metadata import version
import json
import math
from pathlib import Path
import statistics
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.qualify_release import (COUNTERS, canonical_hash, sha,
                                    verify_sealed, verify_benchmark_samples, require, plan)

SERIES = (('A',8,0,'T8, serial'),('B',32,0,'T32, serial'),('C',32,1,'T32, overlap'))
COLORS = dict(A='#a75b22',B='#2871a6',C='#248264')


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def stats(values):
    return dict(minimum=min(values),median=statistics.median(values),maximum=max(values))


def finite(value,name):
    require(type(value) in (int,float) and math.isfinite(value) and value >= 0,'Invalid metric: '+name)
    return value


def dataset(directory, tile, modes):
    directory=Path(directory).resolve()
    saved_plan=load(directory/'plan.json')
    run_plan=saved_plan.get('plan')
    require(isinstance(run_plan,dict) and run_plan.get('samples',0) >= 30 and
            all(mode in run_plan.get('modes',[]) for mode in modes),'Dataset lacks required modes or 30 samples')
    require(run_plan == plan(tuple(run_plan['modes']),run_plan['samples'],run_plan['minimum_continuous_seconds'],
                             run_plan['seed'],run_plan['oracle']), 'Dataset does not use the exact full release-grid plan')
    require(saved_plan.get('host_source_sha256_utf8_lf') and saved_plan.get('bitstream_sha256'),
            'Dataset lacks sealed source/bitstream identities')
    result={}
    for case in run_plan['benchmark_cases']:
        relative=f"benchmark/case_{case['index']:02d}_{case['m']}x{case['n']}x{case['k']}"
        folder=directory/relative
        if not (folder/'seal.json').is_file():
            continue
        binding=dict(plan_sha256=canonical_hash(run_plan),manifest_sha256_bytes=saved_plan['manifest_sha256_bytes'],
                     build_id=saved_plan['build_id'],bitstream_sha256=saved_plan['bitstream_sha256'],
                     host_source_sha256_utf8_lf=saved_plan['host_source_sha256_utf8_lf'],unit=relative)
        report=verify_sealed(folder,binding)
        verify_benchmark_samples(report,case,run_plan)
        fields=list(dict.fromkeys(key for run in report['runs'] for key in run))
        with (folder/'results.csv').open(encoding='utf-8',newline='') as stream:
            reader=csv.DictReader(stream)
            require(reader.fieldnames==fields,'Completed benchmark CSV columns differ')
            rows=list(reader)
        require(rows==[{key:str(run[key]) if key in run else '' for key in fields} for run in report['runs']],
                'Completed benchmark JSON and CSV disagree')
        manifest=report.get('manifest')
        require(isinstance(manifest,dict) and manifest.get('result') == 'PASS' and
                manifest.get('p') == 8 and manifest.get('t') == tile and manifest.get('kmax') == 256 and
                manifest.get('core_hz') == 100000000 and manifest.get('build_id') == saved_plan['build_id'] and
                manifest.get('bitstream_sha256') == saved_plan['bitstream_sha256'],
                'Completed case manifest geometry/build differs')
        require(report.get('source_hashes_unchanged') is True and report.get('transport') ==
                dict(retries=0,rejected_frames=0,poisoned=False),'Completed case has changed sources or transport errors')
        residency=load(folder/'case_000000.json')
        first_run=report['runs'][0]
        require(residency.get('shape') == [case[field] for field in ('m','n','k')] and
                residency.get('seed') == case['seed'] and
                all(residency.get(field) == first_run[field] for field in
                    ('input_a_sha256_bytes','raw_b_sha256_bytes','oracle_sha256_bytes')),
                'Resident input preparation differs from measured jobs')
        for field in ('preparation_seconds','oracle_seconds','input_upload_seconds'):
            finite(residency[field],field)
        for run in report['runs']:
            for field in ('core_seconds','useful_gops','useful_utilization','resident_host_seconds',
                          'c_initialize_seconds','configure_seconds','job_wall_seconds',
                          'allocation_download_seconds','validation_seconds'):
                finite(run[field],field)
            hz=run['core_hz']
            cycles=run['job_cycles']
            for field,wanted in (('core_seconds',cycles/hz),('useful_gops',2*run['m']*run['n']*run['k']*hz/cycles/1e9),
                                 ('useful_utilization',run['m']*run['n']*run['k']/(64*cycles))):
                require(math.isclose(run[field],wanted,rel_tol=1e-12,abs_tol=1e-12),'Derived metric differs: '+field)
        result[case['index']]=dict(case=case,report=report,seal_sha256_bytes=sha(folder/'seal.json'),
                                 results_sha256_bytes=sha(folder/'results.json'),path=str(folder),manifest=manifest,
                                 residency=residency)
    return dict(path=str(directory),plan=saved_plan,cases=result)


def collect(t8, t32, allow_partial=False):
    first=dataset(t8,8,(0,))
    second=dataset(t32,32,(0,1))
    first_cases,second_cases=first['cases'],second['cases']
    common=sorted(set(first_cases)&set(second_cases))
    expected=set(range(16))
    require(common,'No common completed benchmark cases')
    require(allow_partial or set(common) == expected,'Complete comparison requires all 16 cases; use --allow-partial for an explicitly incomplete subset')
    reference=None
    rows,comparisons,inputs=[],[],[]
    for index in common:
        a,b=first_cases[index],second_cases[index]
        require(a['case'] == b['case'],'Compared shape/input seed differs')
        for field in ('p','core_hz','baud','read_slots','part','id','version','kind','enable_overlap',
                      'configuration_sha256_utf8_lf','source_sha256_utf8_lf'):
            require(a['manifest'].get(field) == b['manifest'].get(field),'Comparison changes more than T/MODE: '+field)
        require(a['report']['binding']['host_source_sha256_utf8_lf'] == b['report']['binding']['host_source_sha256_utf8_lf'],
                'Compared host execution sources differ')
        require(first['plan']['plan']['samples'] == second['plan']['plan']['samples'] and
                first['plan']['plan']['seed'] == second['plan']['plan']['seed'],
                'Compared sample counts or base seed differ')
        per_series={}
        for letter,tile,mode,label in SERIES:
            item=a if letter=='A' else b
            runs=[run for run in item['report']['runs'] if run['mode']==mode]
            require(len(runs) >= 30,'Series lacks 30 completed samples')
            signature=[(run['sample'],run['seed'],run['input_a_sha256_bytes'],run['raw_b_sha256_bytes'],
                        run['oracle_sha256_bytes'],run['c_before_sha256_bytes'],
                        {key:value for key,value in run['descriptor'].items() if key!='mode'}) for run in runs]
            if letter=='A': reference=signature
            else: require(signature == reference,'Compared input bytes, C initialization, layout or sample identity differ')
            metrics={field:stats([run[field] for run in runs]) for field in tuple(COUNTERS)+
                     ('core_seconds','useful_gops','useful_utilization','resident_host_seconds',
                      'c_initialize_seconds','configure_seconds','job_wall_seconds',
                      'allocation_download_seconds','validation_seconds')}
            # Accepted beat bytes count full AXI beats, while useful write bytes
            # exclude tail lanes disabled by WSTRB. Neither includes PHY overhead.
            metrics['accepted_axi_beat_bytes']=stats([8*(run['read_beats']+run['write_beats']) for run in runs])
            metrics['read_plus_useful_write_bytes']=stats([8*run['read_beats']+run['write_valid_bytes'] for run in runs])
            entry=dict(case_index=index,m=a['case']['m'],n=a['case']['n'],k=a['case']['k'],series=letter,label=label,
                       mode=mode,tile=tile,samples=len(runs),build_id=item['manifest']['build_id'],
                       bitstream_sha256=item['manifest']['bitstream_sha256'],metrics=metrics,
                       resident_input_preparation={field:item['residency'][field] for field in
                           ('preparation_seconds','oracle_seconds','input_upload_seconds')},
                       residency_scope='One A/BT upload before all samples and modes in this sealed case; C is initialized for every job',
                       raw_counters=[{field:run[field] for field in COUNTERS} for run in runs])
            rows.append(entry)
            per_series[letter]=entry
        comparisons.append(dict(case_index=index,shape=[a['case'][field] for field in ('m','n','k')],
            reuse_cycle_ratio=per_series['A']['metrics']['job_cycles']['median']/per_series['B']['metrics']['job_cycles']['median'],
            overlap_cycle_ratio=per_series['B']['metrics']['job_cycles']['median']/per_series['C']['metrics']['job_cycles']['median'],
            reuse_input_byte_ratio=per_series['A']['metrics']['read_beats']['median']/per_series['B']['metrics']['read_beats']['median'],
            transfer_byte_ratio=per_series['A']['metrics']['read_plus_useful_write_bytes']['median']/per_series['B']['metrics']['read_plus_useful_write_bytes']['median']))
        for label,item in (('T8',a),('T32',b)):
            inputs.append(dict(case_index=index,series_build=label,path=item['path'],
                               seal_sha256_bytes=item['seal_sha256_bytes'],results_sha256_bytes=item['results_sha256_bytes']))
    return dict(schema_version=1,result='PASS',kind='controlled_gemm_reuse_overlap_comparison',
        full_grid_complete=set(common)==expected,completed_case_indexes=common,
        missing_case_indexes=sorted(expected-set(common)),common_cases=len(common),series=rows,comparisons=comparisons,
        input_provenance=inputs,plan_sha256_bytes=dict(t8=sha(Path(t8)/'plan.json'),t32=sha(Path(t32)/'plan.json')),
        measurement_scope='DDR-resident job counters through final successful result write response; host transfer/validation times remain separate',
        comparison_scope='A to B changes T8 to T32 in MODE0; B to C changes only MODE on the same T32 bitstream',
        traffic_scope='Accepted AXI beats and WSTRB bytes; excludes PHY granularity, command overhead and host traffic',
        partial_scope='Only common sealed completed cases; absent cases are listed and never interpolated',
        collector_sha256_bytes=sha(Path(__file__)),utc_recorded=datetime.now(timezone.utc).isoformat())


def write_tables(directory,result):
    (directory/'comparison.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8',newline='\n')
    rows=[]
    for item in result['series']:
        row={key:item[key] for key in ('case_index','m','n','k','series','label','mode','tile','samples','build_id','bitstream_sha256')}
        for field,values in item['metrics'].items():
            row.update({field+'_'+stat:value for stat,value in values.items()})
        rows.append(row)
    with (directory/'comparison.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plot(directory,result):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,
                         'figure.dpi':150,'savefig.dpi':180,'axes.grid':True,'grid.alpha':.2})
    figures=[]
    fig,axes=plt.subplots(1,3,figsize=(12,4.3),sharey=True)
    for axis,k in zip(axes,(16,64,256)):
        for letter,_,_,label in SERIES:
            selected=sorted((row for row in result['series'] if row['series']==letter and row['m']==row['n'] and row['k']==k),key=lambda row:row['m'])
            if not selected: continue
            x=[row['m'] for row in selected]
            y=[row['metrics']['useful_gops']['median'] for row in selected]
            low=[median-row['metrics']['useful_gops']['minimum'] for row,median in zip(selected,y)]
            high=[row['metrics']['useful_gops']['maximum']-median for row,median in zip(selected,y)]
            axis.errorbar(x,y,yerr=[low,high],fmt='o-',color=COLORS[letter],label=label,capsize=3,linewidth=1.5)
        axis.set_xscale('log',base=2)
        axis.set_xticks((32,64,128,256),labels=('32','64','128','256'))
        axis.set_xlim(28,288)
        axis.set_title(f'K = {k}')
        axis.set_xlabel('M = N')
        if not any(row['m']==row['n'] and row['k']==k for row in result['series']):
            axis.text(.5,.5,'No completed cases',ha='center',va='center',transform=axis.transAxes,color='#666666')
    axes[0].set_ylabel('Useful GOPS (median, min-max)')
    maximum=max((row['metrics']['useful_gops']['maximum'] for row in result['series'] if row['m']==row['n']),default=1)
    axes[0].set_ylim(0,maximum*1.1)
    handles,labels=next(((axis.get_legend_handles_labels()) for axis in axes if axis.get_legend_handles_labels()[0]),([],[]))
    fig.legend(handles,labels,loc='lower center',ncol=3,frameon=False,bbox_to_anchor=(.5,0))
    qualifier='' if result['full_grid_complete'] else f" - {result['common_cases']}/16 cases completed"
    fig.suptitle('Nexys GEMM - P8 - 100 MHz - DDR-resident jobs'+qualifier,y=.99,fontsize=12)
    fig.tight_layout(rect=(0,.09,1,.88))
    for suffix in ('png','pdf'):
        name='useful_gops.'+suffix
        fig.savefig(directory/name,bbox_inches='tight')
        figures.append(name)
    plt.close(fig)
    cases=result['completed_case_indexes']
    width=.35
    fig,axis=plt.subplots(figsize=(max(8,len(cases)*.65),4.5))
    for letter,offset,label in (('A',-width/2,'T8, serial'),('B',width/2,'T32, serial')):
        rows={row['case_index']:row for row in result['series'] if row['series']==letter}
        axis.bar([index+offset for index in range(len(cases))],
                 [rows[index]['metrics']['read_plus_useful_write_bytes']['median']/1024 for index in cases],
                 width=width,color=COLORS[letter],label=label)
    labels=['x'.join(str(value) for value in next(item for item in result['comparisons'] if item['case_index']==index)['shape']) for index in cases]
    axis.set_xticks(range(len(cases)),labels=labels,rotation=45,ha='right')
    axis.set_yscale('log')
    axis.set_ylabel('Read + useful write bytes per job (KiB, log scale)')
    axis.set_title('Measured transfer volume: operand reuse')
    axis.legend(frameon=False)
    axis.set_axisbelow(True)
    fig.tight_layout()
    for suffix in ('png','pdf'):
        name='transfer_volume.'+suffix
        fig.savefig(directory/name,bbox_inches='tight')
        figures.append(name)
    plt.close(fig)
    tails=[index for index in cases if index >= 12]
    if tails:
        fig,axis=plt.subplots(figsize=(max(7,len(tails)*1.5),4.5))
        width=.24
        for letter,offset,label in (('A',-width,'T8, serial'),('B',0,'T32, serial'),('C',width,'T32, overlap')):
            rows={row['case_index']:row for row in result['series'] if row['series']==letter}
            values=[rows[index]['metrics']['core_seconds'] for index in tails]
            medians=[value['median']*1e6 for value in values]
            errors=[[median-value['minimum']*1e6 for median,value in zip(medians,values)],
                    [value['maximum']*1e6-median for median,value in zip(medians,values)]]
            axis.bar([index+offset for index in range(len(tails))],medians,yerr=errors,
                     width=width,color=COLORS[letter],label=label,capsize=3)
        labels=['x'.join(str(value) for value in next(item for item in result['comparisons'] if item['case_index']==index)['shape']) for index in tails]
        axis.set_xticks(range(len(tails)),labels=labels)
        axis.set_ylabel('DDR-resident job latency (microseconds)')
        axis.set_title('Tail shapes: median latency with min-max range')
        axis.legend(frameon=False)
        axis.set_axisbelow(True)
        fig.tight_layout()
        for suffix in ('png','pdf'):
            name='tail_latency.'+suffix
            fig.savefig(directory/name,bbox_inches='tight')
            figures.append(name)
        plt.close(fig)
    return figures


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--t8',type=Path,required=True,help='release output containing T8 MODE0 samples')
    parser.add_argument('--t32',type=Path,required=True,help='release output containing matched T32 MODE0/1 samples')
    parser.add_argument('--output',type=Path,required=True,help='fresh output directory')
    parser.add_argument('--allow-partial',action='store_true')
    parser.add_argument('--table-only',action='store_true',help='collect JSON/CSV without optional matplotlib')
    args=parser.parse_args(argv)
    try:
        require(not args.output.exists(),'Refusing to overwrite a prior comparison')
        result=collect(args.t8,args.t32,args.allow_partial)
        if not args.table_only:
            import matplotlib
        parent=args.output.resolve().parent
        parent.mkdir(parents=True,exist_ok=True)
        temporary=Path(tempfile.mkdtemp(prefix='benchmark_collect_pending_',dir=parent))
        write_tables(temporary,result)
        figures=[] if args.table_only else plot(temporary,result)
        record=dict(schema_version=1,result='PASS',full_grid_complete=result['full_grid_complete'],
                    missing_case_indexes=result['missing_case_indexes'],input_provenance=result['input_provenance'],
                    python=platform_version(),matplotlib=None if args.table_only else version('matplotlib'),
                    numpy=None if args.table_only else version('numpy'),saved_artifact_sha256_bytes={
                        path.name:sha(path) for path in temporary.iterdir() if path.is_file()})
        (temporary/'manifest.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8',newline='\n')
        require(not args.output.exists(),'Comparison target appeared during collection')
        temporary.rename(args.output.resolve())
        print(f"PASS: {result['common_cases']}/16 common cases; {len(figures)} figures; {args.output}")
        return 0
    except Exception as error:
        print(f'FAIL: {type(error).__name__}: {error}',file=sys.stderr)
        return 1


def platform_version():
    import platform
    return platform.python_version()


if __name__=='__main__':
    raise SystemExit(main())
