"""Run a command with a temporary Windows idle-sleep request.

No power-plan settings are changed. The request ends when this process exits;
an explicit Sleep action, lid close or power loss can still interrupt a run.
Other platforms execute the command without changing power management.
"""
import argparse
import ctypes
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('supply the command after --')
    kernel = None
    if sys.platform == 'win32':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
        kernel.SetThreadExecutionState.restype = ctypes.c_uint32
        if not kernel.SetThreadExecutionState(0x80000001):
            print('Cannot acquire the temporary idle-sleep request', file=sys.stderr)
            return 1
        print('Windows idle sleep inhibited for this command', flush=True)
    code = 1
    try:
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if kernel else {}
        code = subprocess.run(command, stdin=sys.stdin, stdout=sys.stdout,
                              stderr=sys.stderr, **options).returncode
    except OSError as error:
        print(f'Cannot run command: {error}', file=sys.stderr)
    finally:
        if kernel and not kernel.SetThreadExecutionState(0x80000000):
            print('Cannot clear the idle-sleep request; it ends with this process', file=sys.stderr)
            code = code or 1
    return code


if __name__ == '__main__':
    raise SystemExit(main())
