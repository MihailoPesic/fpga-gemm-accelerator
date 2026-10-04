"""Verify archived proof bytes and optionally extract exact raw tool logs."""
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
            output.relative_to(area.parents[1] / 'build')
            require(not output.exists(), 'Log output already exists; choose a fresh directory')
        manifest = json.loads((area / 'manifest.json').read_text(encoding='utf-8'))
        for name, record in manifest['files'].items():
            path = (area / name).resolve()
            path.relative_to(area)
            payload = path.read_bytes()
            require(len(payload) == record['bytes'] and hashlib.sha256(payload).hexdigest() == record['sha256_bytes'],
                    f'Artifact changed: {name}')
        raw_logs = []
        for record in manifest['compressed_logs']:
            payload = gzip.decompress((area / record['stored_path']).read_bytes())
            require(len(payload) == record['raw_bytes'] and hashlib.sha256(payload).hexdigest() == record['raw_sha256_bytes'],
                    f'Decompressed tool log changed: {record["raw_name"]}')
            require(Path(record['raw_name']).name == record['raw_name'], 'Invalid raw log name')
            raw_logs.append((record['raw_name'], payload))
        require(len(raw_logs) == 22, 'Expected 22 actual tool logs')
        if output:
            output.mkdir(parents=True)
            for name, payload in raw_logs:
                (output / name).write_bytes(payload)
        print(f'PASS {len(manifest["files"])} artifact hashes; 22 exact decompressed tool logs')
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, gzip.BadGzipFile) as error:
        print(f'FAIL {type(error).__name__}: {error}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
