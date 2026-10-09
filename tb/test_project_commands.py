"""Check public command routing with GNU Make; never runs Vivado or the board."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProjectCommands(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.make = shutil.which('make')
        if cls.make is None:
            raise unittest.SkipTest('GNU Make is required; use the portable Linux/WSL environment')

    def run_make(self, *arguments, dry_run=True, expected_status=0):
        environment = os.environ.copy()
        # External Make settings must not alter command-line precedence or
        # silently turn the real help check into another dry run.
        for name in ('MAKEFLAGS', 'GNUMAKEFLAGS', 'MFLAGS', 'MAKEFILES', 'BOARD', 'GEMM_P',
                     'MANIFEST', 'LEGACY_MANIFEST', 'RELEASE_BUILD', 'RELEASE_OUTPUT',
                     'RELEASE_P', 'RELEASE_T', 'RELEASE_BAUD', 'RELEASE_READ_SLOTS',
                     'RELEASE_MODE', 'PYTHON', 'VIVADO', 'PORT'):
            environment.pop(name, None)
        command = [self.make, '--no-print-directory']
        if dry_run:
            command.append('-n')
        result = subprocess.run(command+list(arguments), cwd=ROOT, env=environment,
                                capture_output=True, text=True, check=False, timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.assertEqual(result.returncode, expected_status, result.stdout+result.stderr)
        return [shlex.split(line) for line in result.stdout.splitlines() if line.strip()]

    def routed(self, script, *arguments):
        commands = self.run_make(*arguments)
        matches = [command for command in commands if script in command]
        self.assertEqual(len(matches), 1, commands)
        return matches[0]

    def option(self, command, name):
        self.assertIn(name, command)
        return command[command.index(name)+1]

    def test_bare_make_prints_help_without_running_tools(self):
        # If the default regresses to bitstream, either sentinel executable
        # would fail this real Make invocation instead of touching hardware.
        lines = self.run_make('PYTHON=/must-not-run/python',
                              'VIVADO=/must-not-run/vivado', dry_run=False)
        self.assertTrue(any(line[:2] == ['Current', 'build:'] for line in lines), lines)

    def test_current_build_and_consumers_share_default_manifest(self):
        commands = self.run_make('bitstream')
        self.assertEqual(len(commands), 2)
        self.assertEqual([self.option(command, '--stage') for command in commands],
                         ['sim', 'bitstream'])
        for command in commands:
            self.assertEqual(self.option(command, '--build-dir'), 'build/gemm_current')
            self.assertEqual(self.option(command, '--p'), '8')
            self.assertEqual(self.option(command, '--t'), '32')
            self.assertEqual(self.option(command, '--read-slots'), '4')
            self.assertEqual(self.option(command, '--baud'), '1000000')
            self.assertIn('--overlap', command)
        for target, script in (('demo', 'host.gemm'), ('bench', 'scripts/qualify_release.py'),
                               ('hw-release', 'scripts/qualify_release.py')):
            with self.subTest(target=target):
                command = self.routed(script, target, 'PORT=COM99')
                self.assertEqual(self.option(command, '--manifest'), 'build/gemm_current/build.json')
                self.assertEqual(self.option(command, '--port'), 'COM99')
        self.assertEqual(self.option(self.routed('host.gemm', 'demo', 'PORT=COM99'), '--mode'), '1')

    def test_spaces_and_release_build_override_reach_each_stage(self):
        python, vivado = '/opt/host environment/python', '/opt/vendor tools/vivado'
        assignments = ('PYTHON='+python, 'VIVADO='+vivado, 'RELEASE_BUILD=build/board release',
                       'RELEASE_OUTPUT=build/new results', 'RELEASE_T=8', 'PORT=COM99')
        for command in self.run_make('bitstream', *assignments):
            self.assertEqual(command[0], python)
            self.assertEqual(self.option(command, '--vivado'), vivado)
            self.assertEqual(self.option(command, '--build-dir'), 'build/board release')
            self.assertEqual(self.option(command, '--t'), '8')
        for target, script in (('demo', 'host.gemm'), ('bench', 'scripts/qualify_release.py'),
                               ('hw-release', 'scripts/qualify_release.py')):
            with self.subTest(target=target):
                command = self.routed(script, target, *assignments)
                self.assertEqual(command[0], python)
                self.assertEqual(self.option(command, '--manifest'), 'build/board release/build.json')
                self.assertEqual(self.option(command, '--output'), 'build/new results')
                if target != 'demo':
                    self.assertEqual(command[1:4], ['scripts/keep_awake.py', '--', python])
                    self.assertEqual(self.option(command, '--modes'), 'both')
                    self.assertEqual(self.option(command, '--phase'),
                                     'benchmark' if target == 'bench' else 'all')

    def test_explicit_manifest_selects_archived_current_image(self):
        for target, script in (('demo', 'host.gemm'), ('bench', 'scripts/qualify_release.py'),
                               ('hw-release', 'scripts/qualify_release.py')):
            with self.subTest(target=target):
                command = self.routed(script, target, 'PORT=COM99',
                                      'MANIFEST=build/saved image/build.json')
                self.assertEqual(self.option(command, '--manifest'), 'build/saved image/build.json')

    def test_legacy_default_and_manifest_overrides_remain_independent(self):
        script = 'scripts/hw_test_ddr_gemm.py'
        command = self.routed(script, 'hw-test', 'PORT=COM99',
                              'RELEASE_BUILD=build/current changed')
        self.assertEqual(self.option(command, '--manifest'), 'build/gemm/build.json')
        self.assertEqual(self.option(command, '--p'), '4')
        command = self.routed(script, 'hw-test', 'PORT=COM99', 'GEMM_P=8',
                              'LEGACY_MANIFEST=build/serial image/build.json')
        self.assertEqual(self.option(command, '--manifest'), 'build/serial image/build.json')
        self.assertEqual(self.option(command, '--p'), '8')
        command = self.routed(script, 'hw-test', 'PORT=COM99',
                              'LEGACY_MANIFEST=build/serial image/build.json',
                              'MANIFEST=build/explicit old image/build.json')
        self.assertEqual(self.option(command, '--manifest'), 'build/explicit old image/build.json')
        # Target-local legacy selection must not leak into a sibling demo.
        commands = self.run_make('hw-test', 'demo', 'PORT=COM99')
        self.assertEqual(self.option(commands[0], '--manifest'), 'build/gemm/build.json')
        self.assertEqual(self.option(commands[1], '--manifest'), 'build/gemm_current/build.json')
        for target, stage in (('sim-gemm-ddr', 'sim'), ('ddr-gemm-bitstream', 'bitstream')):
            command = self.routed('scripts/build_ddr_gemm.py', target)
            self.assertEqual(self.option(command, '--stage'), stage)
            self.assertNotIn('--overlap', command)
            self.assertNotIn('--p', command)
            self.assertNotIn('--read-slots', command)
            self.assertNotIn('--baud', command)

    def test_missing_port_and_wrong_board_reject_without_tool_commands(self):
        for target in ('demo', 'bench', 'hw-release', 'hw-test'):
            with self.subTest(target=target):
                self.assertEqual(self.run_make(target, expected_status=2), [])
        self.assertEqual(self.run_make('bitstream', 'BOARD=wrong_board', expected_status=2), [])


if __name__ == '__main__':
    unittest.main()
