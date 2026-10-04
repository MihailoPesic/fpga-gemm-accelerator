set -eu
unset PYTHONPATH
export PYTHONNOUSERSITE=1
cd '/mnt/c/Users/mihai/Documents/continium/accelerator nexys'
test ! -e build/formal-linux-env_20261004
python3 -m venv --without-pip build/formal-linux-env_20261004
