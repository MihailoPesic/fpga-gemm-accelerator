set -eu
unset PYTHONPATH
export PYTHONNOUSERSITE=1
cd '/mnt/c/Users/mihai/Documents/continium/accelerator nexys'
export PIP_CACHE_DIR="$PWD/build/formal_linux_pip_cache"
export PIP_DISABLE_PIP_VERSION_CHECK=1
.venv/bin/python -m pip --python build/formal-linux-env_20261004/bin/python install -r requirements-formal.txt
