"""Verify the Linux row-planner archive and optionally extract raw logs."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extract-logs', type=Path, help='fresh directory inside repository build/')
    args = parser.parse_args()
    area = Path(__file__).resolve().parent
    output = args.extract_logs.resolve() if args.extract_logs else None
    try:
        if output:
            output.relative_to(area.parents[3] / 'build')
            require(not output.exists(), 'Log output already exists; choose a fresh directory')
        manifest = json.loads((area / 'manifest.json').read_text(encoding='utf-8'))
        for name, record in manifest['files'].items():
            path = (area / name).resolve()
            path.relative_to(area)
            payload = path.read_bytes()
            require(len(payload) == record['bytes'] and hashlib.sha256(payload).hexdigest() == record['sha256_bytes'],
                    f'Artifact changed: {name}')
        logs = []
        for record in manifest['compressed_logs']:
            payload = gzip.decompress((area / record['stored_path']).read_bytes())
            require(len(payload) == record['raw_bytes'] and hashlib.sha256(payload).hexdigest() == record['raw_sha256_bytes'],
                    f'Decompressed log changed: {record["raw_name"]}')
            require(Path(record['raw_name']).name == record['raw_name'], 'Invalid raw log name')
            logs.append((record['raw_name'], payload))
        require(len(logs) == 11, 'Expected ten tool logs and one native-wrapper stdout log')
        if output:
            output.mkdir(parents=True)
            for name, payload in logs:
                (output / name).write_bytes(payload)
        print(f'PASS {len(manifest["files"])} artifact hashes; 11 exact decompressed logs')
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, gzip.BadGzipFile) as error:
        print(f'FAIL {type(error).__name__}: {error}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
