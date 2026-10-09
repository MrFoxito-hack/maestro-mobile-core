"""Build C5 SMF2 from a hash-verified observer archive while preserving C3.

Run on Core: python3 build_c5_smf.py SOURCE OBSERVER_BASE NEW_OUTPUT
Only CHF paths and packet measurement plumbing are patched. Frozen sources,
libraries, archives and installed binaries are checked and never overwritten.
"""
import difflib
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


def replace(text, old, new, count=1):
    assert text.count(old) == count, ('anchor mismatch', old, text.count(old))
    return text.replace(old, new)


def patched(name, text):
    text = '#include "c5_packets.h"\n' + text
    if name == 'chf-build.c':
        text = replace(text, 'cJSON_AddNumberToObject(requested, "totalVolume", config->requested_units);',
            'cJSON_AddNumberToObject(requested, c5_packet_mode(sess->chf) ? "serviceSpecificUnits" : "totalVolume", config->requested_units);')
        text = replace(text, '        cJSON_AddNumberToObject(container, "localSequenceNumber", usage->sequence);',
            '''        if (c5_packet_mode(sess->chf)) {
            uint64_t packets = c5_packet_units(sess->chf, usage->sequence);
            if (packets == UINT64_MAX) { cJSON_Delete(root); ogs_sbi_request_free(request); return NULL; }
            cJSON_AddNumberToObject(container, "serviceSpecificUnits", packets);
        }
        cJSON_AddNumberToObject(container, "localSequenceNumber", usage->sequence);''')
    elif name == 'chf-path.c':
        text = replace(text, '    ogs_free(chf);', '    c5_packet_free(chf);\n    ogs_free(chf);')
        text = replace(text, '    sess->chf->refs = 1;', '    sess->chf->refs = 1;\n    c5_packet_enable(sess);')
        text = replace(text, '!json_uint(grant, "totalVolume", 9007199254740991ULL, &granted)',
            '!json_uint(grant, c5_packet_mode(sess->chf) ? "serviceSpecificUnits" : "totalVolume", 9007199254740991ULL, &granted)')
        text = replace(text, 'if (sess->chf->final_units || sess->chf->closing ||',
            'if ((sess->chf->final_units && !c5_packet_mode(sess->chf)) || sess->chf->closing ||')
    elif name == 'chf-accounting.c':
        text = replace(text, '    if (smf_chf_journal(chf, "pfcp_usage", NULL, evidence, 0) != OGS_OK) return OGS_ERROR;',
            '''    if (c5_packet_mode(chf)) {
        uint64_t packets = c5_packet_units(chf, sequence);
        if (packets == UINT64_MAX) return OGS_ERROR;
        ogs_snprintf(evidence, sizeof(evidence), "{\\"sequence\\":%u,\\"uplink\\":%" PRIu64 ",\\"downlink\\":%" PRIu64 ",\\"serviceSpecificUnits\\":%" PRIu64 "}", sequence, uplink, downlink, packets);
    }
    if (smf_chf_journal(chf, "pfcp_usage", NULL, evidence, 0) != OGS_OK) return OGS_ERROR;''')
        text = replace(text, '        cJSON_AddNumberToObject(container, "localSequenceNumber", report->sequence);',
            '''        if (c5_packet_mode(chf)) {
            uint64_t packets = c5_packet_units(chf, report->sequence);
            if (packets == UINT64_MAX) { cJSON_Delete(root); return OGS_ERROR; }
            cJSON_AddNumberToObject(container, "serviceSpecificUnits", packets);
        }
        cJSON_AddNumberToObject(container, "localSequenceNumber", report->sequence);''')
    elif name == 'chf-urr.c':
        text = replace(text, '    urr = bearer->urr;', '    if (c5_packet_mode(sess->chf)) return c5_packet_urr(sess, bearer);\n    urr = bearer->urr;', count=2)
    elif name == 'n4-handler.c':
        text = replace(text, '                bool chf_ok = smf_chf_handle_usage_report(sess, &rep_trig,',
            '''                if (c5_packet_observe(sess->chf, &volume, use_rep->ur_seqn.u32) != OGS_OK) {
                    cause_value = OGS_PFCP_CAUSE_MANDATORY_IE_INCORRECT;
                    goto chf_handled;
                }
                bool chf_ok = smf_chf_handle_usage_report(sess, &rep_trig,''')
    elif name == 'gsm-sm.c':
        text = replace(text, '                        final_ul = vol.uplink_volume;',
            '''                        if (c5_packet_observe(sess->chf, &vol, ur->ur_seqn.u32) != OGS_OK) {
                            final_valid = false;
                            continue;
                        }
                        final_ul = vol.uplink_volume;''')
    else:
        raise ValueError(name)
    return text


def main():
    source, base, out = (Path(x).resolve() for x in sys.argv[1:])
    out.mkdir(exist_ok=False)
    build = source / 'build'
    base_manifest = json.loads((base / 'manifest.json').read_text())
    assert hashlib.sha256((base / 'open5gs-smfd').read_bytes()).hexdigest() == base_manifest['binaries']['smf']
    manifest = {'base': str(base), 'originalHashes': {}, 'changedObjects': []}
    archive = out / 'libsmf.a'
    shutil.copy2(base / 'libsmf.a', archive)
    for name in ('c5_packets.h', 'c5_packets.c', 'c5_packets_test.c'):
        shutil.copy2(Path(__file__).with_name(name), out / name)
    commands = [shlex.split(x) for x in subprocess.check_output(['ninja', '-t', 'commands', 'src/smf/open5gs-smfd'], cwd=build, text=True).splitlines()]
    def compile_file(name, candidate):
        template = 'chf-accounting.c' if name.startswith('c5_') else name
        command = next(c.copy() for c in commands if c[-1] == '../src/smf/' + template)
        obj = out / (name + '.o' if name.startswith('c5_') else Path(command[command.index('-o') + 1]).name)
        command[command.index('-o') + 1] = str(obj)
        for flag in ('-MF', '-MQ'):
            if flag in command:
                command[command.index(flag) + 1] = str(obj) + ('.d' if flag == '-MF' else '')
        command[1:1] = ['-I' + str(out), '-I' + str(base), '-I' + str(base / 'smf'), '-I' + str(source / 'src/smf')]
        command[-1] = str(candidate)
        subprocess.run(command, cwd=build, check=True)
        return obj
    diff = []
    for name in ('chf-build.c','chf-path.c','chf-accounting.c','chf-urr.c','n4-handler.c','gsm-sm.c'):
        original = base / 'smf' / name
        if not original.exists():
            original = source / 'src/smf' / name
        manifest['originalHashes'][str(original)] = hashlib.sha256(original.read_bytes()).hexdigest()
        text = original.read_text()
        candidate = out / name
        candidate.write_text(patched(name, text))
        diff.extend(difflib.unified_diff(text.splitlines(True), candidate.read_text().splitlines(True), fromfile='a/'+name, tofile='b/'+name))
        obj = compile_file(name, candidate)
        subprocess.run(['ar','r',str(archive),str(obj)], check=True)
        manifest['changedObjects'].append(obj.name)
    obj = compile_file('c5_packets.c', out / 'c5_packets.c')
    subprocess.run(['ar','r',str(archive),str(obj)],check=True)
    link = next(c.copy() for c in commands if '-o' in c and c[c.index('-o')+1] == 'src/smf/open5gs-smfd')
    link[link.index('-o')+1] = str(out / 'open5gs-smfd')
    link[link.index('src/smf/libsmf.a')] = str(archive)
    subprocess.run(link,cwd=build,check=True)
    test_obj = compile_file('c5_packets_test.c', out / 'c5_packets_test.c')
    test_link = [str(test_obj) if x == 'src/smf/open5gs-smfd.p/app.c.o' else x for x in link if x != 'src/smf/open5gs-smfd.p/.._main.c.o']
    assert str(test_obj) in test_link
    test_link[test_link.index('-o')+1] = str(out / 'c5-packets-test')
    subprocess.run(test_link, cwd=build, check=True)
    subprocess.run([str(out / 'c5-packets-test')], env=os.environ | {'LD_LIBRARY_PATH':str(base / 'lib')}, check=True)
    legacy_obj = compile_file('c5_legacy_test.c', source / 'src/smf/chf-unit.c')
    legacy_link = [str(legacy_obj) if x == str(test_obj) else x for x in test_link]
    legacy_link[legacy_link.index('-o')+1] = str(out / 'chf-legacy-test')
    subprocess.run(legacy_link, cwd=build, check=True)
    subprocess.run([str(out / 'chf-legacy-test')], env=os.environ | {'LD_LIBRARY_PATH':str(base / 'lib')}, check=True)
    manifest['preservedObjects'] = []
    for member in subprocess.check_output(['ar','t',str(base / 'libsmf.a')],text=True).splitlines():
        if member in manifest['changedObjects']:
            continue
        old = subprocess.check_output(['ar','p',str(base / 'libsmf.a'),member])
        new = subprocess.check_output(['ar','p',str(archive),member])
        assert old == new, 'Unrelated native object changed: ' + member
        manifest['preservedObjects'].append(member)
    for filename,digest in manifest['originalHashes'].items():
        assert hashlib.sha256(Path(filename).read_bytes()).hexdigest()==digest
    (out / 'c5.patch').write_text(''.join(diff))
    manifest['binarySha256'] = hashlib.sha256((out / 'open5gs-smfd').read_bytes()).hexdigest()
    (out / 'manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps(manifest,indent=2))


if __name__ == '__main__':
    main()
