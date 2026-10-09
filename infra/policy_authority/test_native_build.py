"""Run native guard tests against the same Open5GS compiler/headers/libraries.

Run on Core: python3 test_native_build.py SOURCE NEW_OUTPUT.
The sources must be adjacent to this script. No running NF is modified.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess


def test(source, output):
    output.mkdir(exist_ok=False)
    here = Path(__file__).resolve().parent
    build = source / 'build'
    commands = [shlex.split(line) for line in subprocess.check_output(
        ['ninja', '-t', 'commands', 'src/smf/open5gs-smfd'], cwd=build, text=True).splitlines()]
    original = next(c for c in commands if c[-1] == '../src/smf/init.c')
    results = {}
    for name, dependencies in {
        'fence': ['native_fence.c'],
        'control': ['native_fence.c', 'native_control.c', 'native_completion.c'],
        'pfcp': ['native_pfcp.c'],
        'pfcp_dispatch': ['native_fence.c', 'native_control.c', 'native_pfcp.c', 'native_completion.c'],
        'completion': ['native_fence.c', 'native_completion.c'],
        'smf_deferred': ['native_fence.c', 'native_control.c', 'native_completion.c',
                         'native_deferred.c', 'native_smf_deferred.c'],
        'coverage': ['native_fence.c', 'native_control.c', 'native_completion.c'],
        'sbi_deferred': ['native_fence.c', 'native_control.c', 'native_completion.c', 'native_deferred.c'],
    }.items():
        objects = []
        for index, filename in enumerate([*dependencies, 'test_native_' + name + '.c']):
            command = original.copy()
            obj = output / f'{name}-{index}.o'
            command[command.index('-o') + 1] = str(obj)
            for flag in ('-MF', '-MQ'):
                if flag in command:
                    command[command.index(flag) + 1] = str(obj) + ('.d' if flag == '-MF' else '')
            command[1:1] = ['-I' + str(here), '-I' + str(source / 'src/smf'),
                             '-UNDEBUG', '-fsanitize=address,undefined']
            command[-1] = str(here / filename)
            subprocess.run(command, cwd=build, check=True)
            objects.append(str(obj))
        binary = output / ('test-' + name)
        subprocess.run(['cc', '-fsanitize=address,undefined', *objects,
            '-L' + str(build / 'lib/core'), '-logscore', '-ltalloc', '-o', str(binary)], check=True)
        completed = subprocess.run([str(binary)], env=os.environ | {'LD_LIBRARY_PATH': str(build / 'lib/core')},
                                   text=True, capture_output=True)
        results[name] = {'exit_code': completed.returncode, 'stdout': completed.stdout, 'stderr': completed.stderr}
        (output / 'tests.json').write_text(json.dumps(results, indent=2))
        if completed.returncode:
            raise RuntimeError(json.dumps(results[name]))
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(test(args.source.resolve(), args.output.resolve()), indent=2))
