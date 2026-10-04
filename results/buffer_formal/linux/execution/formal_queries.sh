set -eu
unset PYTHONPATH
export PYTHONNOUSERSITE=1
cd '/mnt/c/Users/mihai/Documents/continium/accelerator nexys'
build/formal-linux-env_20261004/bin/python scripts/formal_buffers.py --build-dir build/formal_buffers_linux_final_20261004
