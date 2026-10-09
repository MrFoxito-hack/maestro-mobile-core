"""Run the CHF suite in an isolated directory on Core, including POSIX tests."""
import json

from e2e_native import Lab, ROOT, CHF
from lab_command import get_settings


def main():
    core = Lab(get_settings(), 2222)
    try:
        directory = core.run(['mktemp', '-d', '/home/emsadmin/c5-chf-tests-XXXXXX']).strip()
        files = [*sorted((ROOT / 'chf/app').glob('*.py')),
                 *sorted((ROOT / 'chf/tests').glob('*.py')),
                 *sorted((ROOT / 'chf/tools').glob('*.py')),
                 *sorted((ROOT / '.work/charging-contract').glob('*.yaml'))]
        parents = set()
        for file in files:
            relative = file.relative_to(ROOT).as_posix()
            parent = relative.rsplit('/', 1)[0]
            if parent not in parents:
                core.run(['mkdir', '-p', directory + '/' + parent])
                parents.add(parent)
            core.write(directory + '/' + relative, file.read_bytes().replace(b'\r\n', b'\n'))
        result = core.run(['env', 'PYTHONPATH=' + directory + '/chf', CHF + '/.venv/bin/python',
                           '-m', 'pytest', directory + '/chf/tests', '-q', '-ra'], check=False, timeout=60)
        (ROOT / '.work/c5-charging/evidence/pytest-core.txt').write_text(result, encoding='utf-8')
        print(result)
    finally:
        core.client.close()


if __name__ == '__main__':
    main()
