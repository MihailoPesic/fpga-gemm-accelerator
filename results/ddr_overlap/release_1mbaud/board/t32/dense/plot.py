"""Plot the sealed 256-cube samples with optional Matplotlib dependencies."""

import argparse
import hashlib
import json
from pathlib import Path
import runpy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True,
                        help='Fresh directory for the PNG/PDF and rendering receipt')
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    runpy.run_path(str(directory / 'verify.py'))['verify'](directory)
    if args.output.exists():
        raise ValueError('Plot output directory already exists')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    summary = json.loads((directory / 'summary.json').read_bytes())
    groups = [summary['distributions'][str(mode)]['metrics'] for mode in (0, 1)]
    medians = [group['useful_gops']['median'] for group in groups]
    bounds = [[group['useful_gops']['median'] - group['useful_gops']['minimum']
               for group in groups],
              [group['useful_gops']['maximum'] - group['useful_gops']['median']
               for group in groups]]
    compute = [group['compute_cycles']['median'] for group in groups]
    remaining = [group['job_cycles']['median'] - active
                 for group, active in zip(groups, compute)]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.8), constrained_layout=True)
    colors = ['#306998', '#d17a27']
    axes[0].bar([0, 1], medians, color=colors, width=0.6)
    axes[0].errorbar([0, 1], medians, yerr=bounds, fmt='none', capsize=6, color='black')
    for pos, value in enumerate(medians):
        axes[0].text(pos, value + 0.35, f'{value:.3f}', ha='center')
    axes[0].set(xticks=[0, 1], xticklabels=['Serial', 'Overlap'], ylim=(0, 13),
                ylabel='Useful GOPS', title='Median with minimum/maximum')
    axes[1].bar([0, 1], compute, color='#306998', width=0.6,
                label='Active microtile cycles')
    axes[1].bar([0, 1], remaining, bottom=compute, color='#b7c4cf', width=0.6,
                label='Remaining job cycles')
    for pos, group in enumerate(groups):
        value = group['job_cycles']['median']
        axes[1].text(pos, value + 18000, f'{value:,.1f}', ha='center')
    axes[1].set(xticks=[0, 1], xticklabels=['Serial', 'Overlap'], ylim=(0, 870000),
                ylabel='Median core cycles', title='Recorded JOB_CYCLES decomposition')
    axes[1].ticklabel_format(axis='y', style='plain')
    axes[1].legend(loc='upper right', frameon=False, fontsize=9)
    for axis in axes:
        axis.spines[['top', 'right']].set_visible(False)
        axis.grid(axis='y', alpha=0.2)
        axis.set_axisbelow(True)
    fig.suptitle('DDR-resident INT8 GEMM: 256 x 256 x 256\n'
                 'P8 / T32 / 100 MHz / 30 matched pairs; inputs preloaded')
    fig.text(0.5, -0.025,
             'UART transfer and host validation are excluded. Remaining cycles are not a DDR bandwidth partition.',
             ha='center', fontsize=9)
    args.output.mkdir(parents=True)
    png, pdf = args.output / 'performance.png', args.output / 'performance.pdf'
    fig.savefig(png, dpi=180, bbox_inches='tight', metadata={'Software': 'Matplotlib ' + matplotlib.__version__})
    fig.savefig(pdf, bbox_inches='tight', metadata={'Creator': 'Matplotlib ' + matplotlib.__version__,
                'CreationDate': None, 'ModDate': None})
    plt.close(fig)
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    receipt = dict(schema_version=1, result='PASS', backend=matplotlib.get_backend(),
                   matplotlib=matplotlib.__version__, build_id=0x9d4beb4d,
                   input_summary_sha256_bytes=digest(directory / 'summary.json'),
                   plotting_source_sha256_bytes=digest(Path(__file__)),
                   scope='Single dense DDR-resident case; all 30 paired samples',
                   outputs_sha256_bytes={path.name: digest(path) for path in (png, pdf)})
    (args.output / 'plot_receipt.json').write_text(json.dumps(receipt, indent=2) + '\n',
                                                encoding='utf-8', newline='\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
