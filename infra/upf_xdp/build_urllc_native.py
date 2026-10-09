"""On the Core VM: build a separate UPF using existing ABI-matched objects.

Does not write the original source/build tree or installed NF binaries.
Usage: python3 build_urllc_native.py EXISTING_SOURCE NEW_EMPTY_OUTPUT
"""
import difflib
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


def patched_c4(name, source):
    """Patch only the candidate copies; no policy removal or unmetered profile."""
    include = '#include "context.h"'
    if name == 'init.c':
        assert source.count('    mpo_open();') == source.count('    mpo_close();') == 1
        return source.replace('    mpo_open();', '    mpo_open();\n    maestro_c4_open();').replace(
            '    mpo_close();', '    maestro_c4_close();\n    mpo_close();').replace(
            '#include "native_observer.h"', '#include "native_observer.h"\n#include "c4_bridge.h"')
    assert source.count(include) == 1
    extra = '\n#define MAESTRO_C4_IMPLEMENTATION\n' if name == 'context.c' else '\n'
    text = source.replace(include, include + extra + '#include "c4_native.h"', 1)
    if name == 'n4-handler.c':
        for event in ('Establishment', 'Modification', 'Deletion'):
            needle = f'    ogs_debug("Session {event} Request");'
            assert text.count(needle) == 1
            text = text.replace(needle, '    maestro_c4_before(sess);\n    maestro_c4_revoke();\n' + needle)
        needle = '    return;\n\ncleanup:'
        assert text.count(needle) == 2
        text = text.replace(needle, '    maestro_c4_after(sess);\n    maestro_c4_publish();\n' + needle)
    elif name == 'context.c':
        needle = 'int upf_sess_remove(upf_sess_t *sess)\n{'
        assert text.count(needle) == 1
        text = text.replace(needle, needle + '\n    maestro_c4_forget(sess);\n    maestro_c4_revoke();')
        # Preserve the original single-packet API and all reporting logic, but
        # expose an exact batch operation for cumulative XDP deltas.
        original = 'void upf_sess_urr_acc_add(upf_sess_t *sess, ogs_pfcp_urr_t *urr, size_t size, bool is_uplink)'
        assert text.count(original) == 1
        many = ('void upf_sess_urr_acc_add_many(upf_sess_t *sess, ogs_pfcp_urr_t *urr, '
                'uint64_t size, uint64_t packets, bool is_uplink)')
        wrapper = original + '\n{\n    upf_sess_urr_acc_add_many(sess, urr, size, 1, is_uplink);\n}\n\n'
        text = text.replace(original, wrapper + many)
        for field in ('total_pkts', 'ul_pkts', 'dl_pkts'):
            assert text.count('urr_acc->'+field+'++;') == 1
            text = text.replace('urr_acc->'+field+'++;', 'urr_acc->'+field+' += packets;')
        needle = '    /* Increment total & ul octets + pkts */'
        assert text.count(needle) == 1
        text = text.replace(needle, '''    ogs_assert(packets && size && UINT64_MAX - urr_acc->total_octets >= size &&
        UINT64_MAX - urr_acc->total_pkts >= packets);
''' + needle)
        needle = '    report->type.usage_report = 1;'
        assert text.count(needle) == 1
        text = text.replace(needle, '    maestro_c4_import(sess);\n' + needle)
    elif name == 'gtp-path.c':
        # Every existing charged packet path receives the same admission call.
        calls = [('sess','pdr','false','recvbuf->len','goto cleanup'),
                 ('sess','pdr','true','pkbuf->len','goto cleanup'),
                 ('dst_sess','dl_pdr','false','pkbuf->len','goto cleanup'),
                 ('sess','pdr','uplink','pkbuf->len','goto cleanup'),
                 ('sess','pdr','false','recvbuf->len','break')]
        for sess,pdr,direction,length,failure in calls:
            needle=f'if (!upf_quota_allow({sess}, {pdr}, {direction})) {failure};'
            assert text.count(needle) == 1
            text=text.replace(needle, f'if (!maestro_c4_native_allow({sess}, {pdr}, {length}, {direction})) {failure};\n'
                              f'    if (!upf_quota_allow({sess}, {pdr}, {direction})) {{ '
                              'ogs_debug("C4 native packet rejected by quota guard"); '
                              f'{failure}; }}')
        needle='if (pdr->qer && !maestro_qer_allow('
        assert text.count(needle)==1
        text=text.replace(needle,'if (!maestro_c4_managed(sess, pdr) && pdr->qer && !maestro_qer_allow(')
    elif name == 'pfcp-sm.c':
        needle = '        ogs_info("PFCP de-associated %s",'
        assert text.count(needle) == 1
        text = text.replace(needle, '        maestro_c4_revoke();\n' + needle)
    return text


def patched(name, source):
    text = source.replace('#include "context.h"', '#include "context.h"\n#include "urllc_native.h"', 1)
    assert text != source
    if name == 'n4-handler.c':
        for event in ('Establishment', 'Modification', 'Deletion'):
            needle = f'    ogs_debug("Session {event} Request");'
            assert text.count(needle) == 1
            text = text.replace(needle, '    maestro_xdp_revoke();\n' + needle)
        needle = '    return;\n\ncleanup:'
        assert text.count(needle) == 2
        text = text.replace(needle, '    maestro_xdp_publish(sess);\n' + needle)
    elif name == 'context.c':
        needle = 'void upf_sess_remove_all(void)\n{'
        assert needle in text
        text = text.replace(needle, needle + '\n    maestro_xdp_revoke();')
        # Individual removal also invalidates maps before freeing a context.
        import re
        text, count = re.subn(r'(int upf_sess_remove\(upf_sess_t \*sess\)\n\{)',
                              r'\1\n    maestro_xdp_revoke();', text)
        assert count == 1
    elif name == 'pfcp-sm.c':
        needle = '        ogs_info("PFCP de-associated %s",'
        assert needle in text
        text = text.replace(needle, '        maestro_xdp_revoke();\n' + needle)
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--profile', choices=['c4', 'legacy'], default='c4')
    parser.add_argument('--observer-base', type=Path, help='C3 build matching the installed UPF SHA-256')
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if output == source or source in output.parents:
        raise ValueError('Candidate output must be outside the source/build tree')
    output.mkdir(exist_ok=False)
    build = source / 'build'
    commands = subprocess.check_output(['ninja', '-t', 'commands', 'src/upf/open5gs-upfd'], cwd=build, text=True).splitlines()
    header = 'c4_native.h' if args.profile == 'c4' else 'urllc_native.h'
    shutil.copy2(Path(__file__).with_name(header), output / header)
    if args.profile == 'c4':
        if not args.observer_base:
            raise ValueError('C4 requires --observer-base to retain all C3 observer/fencing objects')
        for header in ('c4_bridge.h','c4_abi.h','c4_bridge.c'):
            shutil.copy2(Path(__file__).with_name(header),output/header)
    archive = output / 'libupf.a'
    original_archive = build / 'src/upf/libupf.a'
    members = subprocess.check_output(['ar', 't', str(original_archive)], text=True).splitlines()
    members = [str(Path(m) if Path(m).is_absolute() else original_archive.parent / m) for m in members]
    # Meson uses thin archives with relative references. Materialize a new full
    # archive so updating it cannot follow or mutate the original object paths.
    if args.profile == 'c4':
        shutil.copy2(args.observer_base/'libupf.a',archive)
    else:
        subprocess.run(['ar', 'cr', str(archive), *members], check=True)
    changes = []
    source_hashes = {}
    names = ('n4-handler.c','context.c','pfcp-sm.c','gtp-path.c','init.c') if args.profile=='c4' else ('n4-handler.c','context.c','pfcp-sm.c')
    for name in names:
        original = (source / 'src/upf' / name).read_text()
        source_hashes[name] = hashlib.sha256((source / 'src/upf' / name).read_bytes()).hexdigest()
        if args.profile=='c4' and name=='pfcp-sm.c': original=(args.observer_base/'upf/pfcp-sm.c').read_text()
        if args.profile=='c4' and name=='init.c': original=(args.observer_base/'upf-init.c').read_text()
        result = (patched_c4 if args.profile == 'c4' else patched)(name, original)
        (output / name).write_text(result)
        changes.extend(difflib.unified_diff(original.splitlines(True), result.splitlines(True),
                                          fromfile='a/src/upf/'+name, tofile='b/src/upf/'+name))
        command = next(shlex.split(line) for line in commands if line.endswith('../src/upf/' + name))
        obj_index = command.index('-o') + 1
        obj = output / Path(command[obj_index]).name
        command[obj_index] = str(obj)
        if '-MF' in command: command[command.index('-MF') + 1] = str(obj) + '.d'
        if '-MQ' in command: command[command.index('-MQ') + 1] = str(obj)
        command[-1] = str(output / name)
        if args.profile=='c4': command[1:1]=['-I'+str(output),'-I'+str(args.observer_base),'-I'+str(source/'src/upf')]
        subprocess.run(command, cwd=build, check=True)
        subprocess.run(['ar', 'r', str(archive), str(obj)], check=True)
    if args.profile=='c4':
        command[-1]=str(output/'c4_bridge.c')
        obj=output/'c4_bridge.c.o';command[command.index('-o')+1]=str(obj)
        for flag in ('-MF','-MQ'):
            if flag in command:command[command.index(flag)+1]=str(obj)+('.d' if flag=='-MF' else '')
        subprocess.run(command,cwd=build,check=True)
        subprocess.run(['ar','r',str(archive),str(obj)],check=True)
    (output / 'native-lifecycle.patch').write_text(''.join(changes))
    link = next(shlex.split(line) for line in commands if line.startswith('c++ ') and '-o src/upf/open5gs-upfd ' in line)
    link[link.index('-o')+1] = str(output / 'open5gs-upfd')
    link[link.index('src/upf/libupf.a')] = str(archive)
    subprocess.run(link, cwd=build, check=True)
    lib = output / 'lib'; lib.mkdir()
    for arg in link:
        path = build / arg
        if path.is_file() and '.so' in path.name and not arg.startswith('/'):
            shutil.copy2(path, lib / path.name)
            short = path.name.split('.so')[0] + '.so'
            for alias in (short, short+'.2'):
                if alias != path.name and not (lib / alias).exists(): (lib / alias).symlink_to(path.name)
    env = os.environ | {'LD_LIBRARY_PATH': str(lib)}
    subprocess.run([str(output/'open5gs-upfd'), '-v'], env=env, check=True)
    for name, digest in source_hashes.items():
        assert hashlib.sha256((source / 'src/upf' / name).read_bytes()).hexdigest() == digest
    manifest = {'binary': str(output/'open5gs-upfd'), 'source_unchanged': True,
                'profile': args.profile, 'source_sha256': source_hashes,
                'binary_sha256': hashlib.sha256((output/'open5gs-upfd').read_bytes()).hexdigest(),
                'admission_ready': False, 'deployed': False,
                'blockers': ['Crash replay/URR timestamp acceptance', 'SUPI/PDU correlation',
                             'Full policy lifecycle and live traffic acceptance']}
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(manifest))


if __name__ == '__main__': main()
