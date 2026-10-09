"""Compile/load C4 in isolated bpffs pins; never attach to an interface."""
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    directory = Path(__file__).resolve().parent
    candidates = sorted(Path('/usr/lib/linux-tools').glob('*/bpftool'))
    bpftool = str(candidates[-1]) if candidates else '/usr/sbin/bpftool'
    def run(*args):
        return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)
    run('cc', '-Wall', '-Wextra', '-Werror', '-O2', '-pthread', str(directory/'test_c4_policy.c'),
        '-o', str(directory/'test_c4_policy'))
    pins = Path(tempfile.mkdtemp(prefix='maestro-c4-test-', dir='/sys/fs/bpf'))
    try:
        (pins/'maps').mkdir()
        (pins/'xmaps').mkdir()
        run(bpftool,'prog','load',str(directory/'c4_policy_kern.o'),str(pins/'policy'),
            'type','xdp','pinmaps',str(pins/'maps'))
        # Reuse the exact policy map in the packet program, not a second bucket.
        run(bpftool,'prog','load',str(directory/'c4_xdp_kern.o'),str(pins/'xdp'),
            'type','xdp','map','name','c4_policy_v1','pinned',str(pins/'maps/c4_policy_v1'),
            'pinmaps',str(pins/'xmaps'))
        p = json.loads(run(bpftool,'-j','prog','show','pinned',str(pins/'policy')))
        x = json.loads(run(bpftool,'-j','prog','show','pinned',str(pins/'xdp')))
        p = p[0] if isinstance(p,list) else p
        x = x[0] if isinstance(x,list) else x
        assert set(p['map_ids']) & set(x['map_ids']), 'Programs do not share the policy map'
        result = json.loads(run(str(directory/'test_c4_policy'),str(pins/'policy'),str(pins/'maps/c4_policy_v1'),str(pins)))
        result.update(kernel=os.uname().release, shared_map_verified=True)
        (directory/'kernel-evidence.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
    except subprocess.CalledProcessError as exc:
        (directory/'verifier-error.log').write_text(exc.output)
        print(exc.output[-6000:])
        raise
    finally:
        # Only the unique directory created by this test. No interface is touched.
        for name in ('xdp','policy'):
            (pins/name).unlink(missing_ok=True)
        for name in ('maps','xmaps'):
            for path in (pins/name).iterdir(): path.unlink()
            (pins/name).rmdir()
        pins.rmdir()


if __name__ == '__main__':
    main()
