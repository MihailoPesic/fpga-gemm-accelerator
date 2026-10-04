set -eu
unset PYTHONPATH
export PYTHONNOUSERSITE=1
cd '/mnt/c/Users/mihai/Documents/continium/accelerator nexys'
.venv/bin/python -m pip --python build/formal-linux-env_20261004/bin/python check
