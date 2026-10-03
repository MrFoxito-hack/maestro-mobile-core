"""Mechanically export the reviewed isolated source diff as a reproducible patch."""
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '.work/open5gs'
BASELINE = '157f611a530e292e40ec50f9d23f0ef5d4fcd6a6'
TARGET = Path(__file__).parent / 'patches/0001-native-nchf.patch'


def git(*args):
    return subprocess.check_output(['git', '-C', str(SOURCE), *args])


def main():
    if git('rev-parse', 'HEAD').decode().strip() != BASELINE:
        raise SystemExit('Native baseline changed; export refused')
    patch = git('diff', '--no-ext-diff', '--binary', 'HEAD')
    untracked = git('ls-files', '--others', '--exclude-standard').decode().splitlines()
    if set(untracked) - {'src/smf/chf-accounting.c'}:
        raise SystemExit('Unreviewed untracked native files; export refused')
    for path in untracked:
        content = (SOURCE / path).read_bytes().replace(b'\r\n', b'\n')
        if not content.endswith(b'\n'):
            raise SystemExit('New source must end with newline')
        lines = content.splitlines(keepends=True)
        patch += (f'diff --git a/{path} b/{path}\nnew file mode 100644\n'
                  f'--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n').encode()
        patch += b''.join(b'+' + line for line in lines)
    with tempfile.TemporaryDirectory(prefix='chf-export-') as temporary:
        candidate = Path(temporary) / 'native.patch'
        candidate.write_bytes(patch)
        subprocess.run(['git', '-C', str(SOURCE), 'apply', '--check', '--reverse', str(candidate)], check=True)
    TARGET.parent.mkdir(exist_ok=True)
    TARGET.write_bytes(patch)
    print(f'Exported {len(patch)} bytes; reverse application checked against reviewed source')


if __name__ == '__main__':
    main()
