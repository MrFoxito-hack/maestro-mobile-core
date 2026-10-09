"""Build isolated PCF/SMF/UPF observers from the CHF-compatible Meson objects.

Run on Core: python3 build_native.py SOURCE NEW_OUTPUT
No installed binary, source, shared library or original archive is modified.
"""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
if __package__:
    from .patch_writers import SOURCES, patch as patch_writer
else:
    from patch_writers import SOURCES, patch as patch_writer


def patch_init(nf, original):
    anchor = 'static ogs_thread_t *thread;'
    start = f'    ogs_fsm_init(&{nf}_sm, {nf}_state_initial, {nf}_state_final, 0);'
    end = f'    ogs_fsm_fini(&{nf}_sm, 0);'
    for marker in (anchor, start, end):
        if original.count(marker) != 1:
            raise ValueError(f'{nf}: unexpected source at {marker}')
    result = original.replace(anchor, f'#define MPO_{nf.upper()} 1\n#include "native_observer.h"\n\n' + anchor)
    result = result.replace(start, start + '\n    mpo_open();')
    result = result.replace(end, '    mpo_close();\n' + end)
    if nf == 'smf':
        pop = '            rv = ogs_queue_trypop(ogs_app()->queue, (void**)&e);'
        dispatch = '            ogs_fsm_dispatch(&smf_sm, e);'
        for marker in (pop, dispatch):
            if result.count(marker) != 1:
                raise ValueError('Unexpected SMF event loop: ' + marker)
        result = result.replace(pop, '''            e = msd_take();
            rv = e ? OGS_OK : ogs_queue_trypop(ogs_app()->queue, (void**)&e);''')
        result = result.replace(dispatch, '            if (msd_hold(e)) continue;\n' + dispatch)
        result = result.replace('    mpo_close();', '    msd_close();\n    mpo_close();')
    if nf in ('pcf', 'smf'):
        pop = ('            e = msd_take();' if nf == 'smf' else
               '            rv = ogs_queue_trypop(ogs_app()->queue, (void**)&e);')
        replacement = ('            e = nsd_take();\n            if (!e) e = msd_take();' if nf == 'smf' else
                       '            e = nsd_take();\n            rv = e ? OGS_OK : ogs_queue_trypop(ogs_app()->queue, (void**)&e);')
        if result.count(pop) != 1:
            raise ValueError('Unexpected SBI event loop: ' + nf)
        result = result.replace(pop, replacement)
        dispatch = ('            if (msd_hold(e)) continue;' if nf == 'smf' else
                    '            ogs_fsm_dispatch(&pcf_sm, e);')
        if result.count(dispatch) != 1:
            raise ValueError('Unexpected SBI dispatch: ' + nf)
        result = result.replace(dispatch, '            if (nsd_hold(e)) continue;\n' + dispatch)
        result = result.replace('    mpo_close();', '    nsd_close();\n    mpo_close();')
    return result


def build(source, output):
    source, output = source.resolve(), output.resolve()
    output.mkdir(exist_ok=False)
    build_dir = source / 'build'
    for name in ('native_observer.h', 'native_fence.h', 'native_fence.c', 'native_control.h', 'native_control.c',
                 'native_pfcp.h', 'native_pfcp.c', 'native_pfcp_ogs.h',
                 'native_completion.h', 'native_completion.c', 'native_qos.h', 'native_qos.c', 'native_pcf.h',
                 'native_deferred.h', 'native_deferred.c', 'native_smf_deferred.h', 'native_smf_deferred.c',
                 'native_sbi_deferred.h', 'native_coverage.h'):
        shutil.copy2(Path(__file__).with_name(name), output / name)
    manifest = {'source': str(source), 'binaries': {}, 'source_hashes': {}, 'libraries': {},
                'coverage_profile': 'ipv4-session-ambr-v1', 'coverage_build': 1,
                'engineering_sources': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in Path(__file__).parent.iterdir() if p.suffix in {'.py', '.c', '.h'}}}
    lib = output / 'lib'
    lib.mkdir()
    for nf in ('pcf', 'smf', 'upf'):
        target = f'src/{nf}/open5gs-{nf}d'
        commands = [shlex.split(line) for line in subprocess.check_output(
            ['ninja', '-t', 'commands', target], cwd=build_dir, text=True).splitlines()]
        old_archive = build_dir / f'src/{nf}/lib{nf}.a'
        archive = output / f'lib{nf}.a'
        members = subprocess.check_output(['ar', 't', str(old_archive)], text=True).splitlines()
        members = [str(Path(m) if Path(m).is_absolute() else old_archive.parent / m) for m in members]
        subprocess.run(['ar', 'cr', str(archive), *members], check=True)
        original_path = source / f'src/{nf}/init.c'
        original = original_path.read_text()
        manifest['source_hashes'][str(original_path)] = hashlib.sha256(original_path.read_bytes()).hexdigest()
        patched = patch_init(nf, original)
        candidate = output / f'{nf}-init.c'
        candidate.write_text(patched)
        (output / f'{nf}.patch').write_text(''.join(difflib.unified_diff(
            original.splitlines(True), patched.splitlines(True),
            fromfile=f'a/src/{nf}/init.c', tofile=f'b/src/{nf}/init.c')))
        command = next(c.copy() for c in commands if c[-1] == f'../src/{nf}/init.c')
        obj = output / nf / Path(command[command.index('-o') + 1]).name
        obj.parent.mkdir()
        command[command.index('-o') + 1] = str(obj)
        for flag in ('-MF', '-MQ'):
            if flag in command:
                command[command.index(flag) + 1] = str(obj) + ('.d' if flag == '-MF' else '')
        # Quoted local includes previously resolved relative to src/<nf>.
        command.insert(1, '-I' + str(source / 'src' / nf))
        command.insert(1, '-DMAESTRO_NATIVE_COVERAGE_V1=1')
        command[-1] = str(candidate)
        subprocess.run(command, cwd=build_dir, check=True)
        subprocess.run(['ar', 'r', str(archive), str(obj)], check=True)
        for name in SOURCES[nf]:
            writer_path = source / 'src' / nf / name
            writer_original = writer_path.read_text()
            manifest['source_hashes'][str(writer_path)] = hashlib.sha256(writer_path.read_bytes()).hexdigest()
            writer_patched = patch_writer(nf, name, writer_original)
            writer_candidate = obj.parent / name
            writer_candidate.write_text(writer_patched)
            (obj.parent / (name + '.patch')).write_text(''.join(difflib.unified_diff(
                writer_original.splitlines(True), writer_patched.splitlines(True),
                fromfile=f'a/src/{nf}/{name}', tofile=f'b/src/{nf}/{name}')))
            if not name.endswith('.c'):
                continue
            writer_command = next(c.copy() for c in commands if c[-1] == f'../src/{nf}/{name}')
            writer_obj = obj.parent / Path(writer_command[writer_command.index('-o') + 1]).name
            writer_command[writer_command.index('-o') + 1] = str(writer_obj)
            for flag in ('-MF', '-MQ'):
                if flag in writer_command:
                    writer_command[writer_command.index(flag) + 1] = str(writer_obj) + ('.d' if flag == '-MF' else '')
            writer_command[1:1] = ['-I' + str(output), '-I' + str(source / 'src' / nf)]
            writer_command[-1] = str(writer_candidate)
            subprocess.run(writer_command, cwd=build_dir, check=True)
            subprocess.run(['ar', 'r', str(archive), str(writer_obj)], check=True)
        for name in ('native_fence.c', 'native_control.c', 'native_pfcp.c', 'native_completion.c',
                     *(('native_deferred.c',) if nf == 'pcf' else ()),
                     *(('native_qos.c', 'native_deferred.c', 'native_smf_deferred.c') if nf == 'smf' else ())):
            extra = command.copy()
            extra_obj = obj.parent / (name + '.o')
            extra[extra.index('-o') + 1] = str(extra_obj)
            for flag in ('-MF', '-MQ'):
                if flag in extra:
                    extra[extra.index(flag) + 1] = str(extra_obj) + ('.d' if flag == '-MF' else '')
            extra[-1] = str(output / name)
            subprocess.run(extra, cwd=build_dir, check=True)
            subprocess.run(['ar', 'r', str(archive), str(extra_obj)], check=True)
        link = next(c.copy() for c in commands if '-o' in c and c[c.index('-o') + 1] == target)
        binary = output / f'open5gs-{nf}d'
        link[link.index('-o') + 1] = str(binary)
        link[link.index(f'src/{nf}/lib{nf}.a')] = str(archive)
        subprocess.run(link, cwd=build_dir, check=True)
        for arg in link:
            path = build_dir / arg
            if path.is_file() and '.so' in path.name and not arg.startswith('/'):
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if path.name in manifest['libraries']:
                    assert manifest['libraries'][path.name] == digest, 'library collision'
                else:
                    shutil.copy2(path, lib / path.name)
                    manifest['libraries'][path.name] = digest
                short = path.name.split('.so')[0] + '.so'
                for alias in (short, short + '.2'):
                    if alias != path.name and not (lib / alias).exists():
                        (lib / alias).symlink_to(path.name)
        subprocess.run([str(binary), '-v'], env=os.environ | {'LD_LIBRARY_PATH': str(lib)}, check=True)
        manifest['binaries'][nf] = hashlib.sha256(binary.read_bytes()).hexdigest()
    for filename, digest in manifest['source_hashes'].items():
        assert hashlib.sha256(Path(filename).read_bytes()).hexdigest() == digest
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    print(json.dumps(build(args.source, args.output), indent=2))
