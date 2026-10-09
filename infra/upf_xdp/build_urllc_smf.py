"""Build a separate SMF3 IPv4 lab profile; never overwrite shared objects/binaries.

Run on Core: python3 build_urllc_smf.py SOURCE NEW_OUTPUT
The opt-in env flag is installed only in the dedicated SMF3 unit.
"""
import difflib
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

HELPER = '''
#include "urllc_lab_policy.h"
static inline bool maestro_urllc_lab(smf_sess_t *sess)
{
    smf_ue_t *ue = smf_ue_find_by_id(sess->smf_ue_id);
    return sess->ue_session_type == OGS_PDU_SESSION_TYPE_IPV4 &&
        maestro_urllc_profile_allowed(getenv("MAESTRO_URLLC_UNMETERED_LAB"),
        smf_self()->chf_config.enabled, sess->epc,
        HOME_ROUTED_ROAMING_IN_HSMF(sess) || HOME_ROUTED_ROAMING_IN_VSMF(sess),
        ue ? ue->supi : NULL, sess->session.name, sess->s_nssai.sst, sess->s_nssai.sd.v);
}
'''


def patched(name, original):
    needle = '#include "context.h"' if name == 'context.c' else '#include "npcf-handler.h"'
    assert original.count(needle) == 1
    text = original.replace(needle, needle + '\n' + HELPER)
    if name == 'context.c':
        start = text.index('    /* URR */\n    urr = ogs_pfcp_urr_add(&sess->pfcp);')
        end = text.index('    /* QER */', start)
        block = text[start:end]
        text = text[:start] + '    if (!maestro_urllc_lab(sess)) {\n' + ''.join('    '+line if line.strip() else line for line in block.splitlines(True)) + '    }\n\n' + text[end:]
    elif name == 'npcf-handler.c':
        needle = '    qer->mbr.uplink = sess->session.ambr.uplink;\n    qer->mbr.downlink = sess->session.ambr.downlink;'
        assert text.count(needle) == 1
        text = text.replace(needle, '    qer->mbr.uplink = maestro_urllc_lab(sess) ? 0 : sess->session.ambr.uplink;\n    qer->mbr.downlink = maestro_urllc_lab(sess) ? 0 : sess->session.ambr.downlink;')
        needle = '    up2cp_pdr->precedence = OGS_PFCP_UP2CP_PDR_PRECEDENCE;'
        assert text.count(needle) == 1
        text = text.replace(needle, needle + '''

    /* This IPv4-only lab session does not forward IPv6 router solicitations
     * to the SMF. Remove the optional CP PDRs before any N4 serialization. */
    if (maestro_urllc_lab(sess)) {
        smf_sess_delete_cp_up_data_forwarding(sess);
        sess->cp2up_pdr = NULL;
        sess->up2cp_pdr = NULL;
        sess->up2cp_far = NULL;
        ogs_info("MAEstro URLLC lab: no URR, open QER without MBR, IPv4 UE-MEC");
    }
''')
    else:
        raise ValueError(name)
    return text


def main():
    source, output = (Path(p).resolve() for p in sys.argv[1:3])
    output.mkdir(exist_ok=False)
    build = source / 'build'
    here = Path(__file__).parent
    for name in ('urllc_lab_policy.h','test_urllc_lab_policy.c'):
        shutil.copy2(here/name,output/name)
    subprocess.run(['cc','-Wall','-Wextra','-Werror',str(output/'test_urllc_lab_policy.c'),'-o',str(output/'test-policy')],check=True)
    subprocess.run([str(output/'test-policy')],check=True)
    commands = subprocess.check_output(['ninja','-t','commands','src/smf/open5gs-smfd'],cwd=build,text=True).splitlines()
    old_archive = build/'src/smf/libsmf.a'
    archive = output/'libsmf.a'
    members = subprocess.check_output(['ar','t',str(old_archive)],text=True).splitlines()
    members = [str(Path(p) if Path(p).is_absolute() else old_archive.parent/p) for p in members]
    subprocess.run(['ar','cr',str(archive),*members],check=True)
    originals, diff = {}, []
    for name in ('context.c','npcf-handler.c'):
        path = source/'src/smf'/name
        originals[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        original = path.read_text()
        result = patched(name,original)
        (output/name).write_text(result)
        diff.extend(difflib.unified_diff(original.splitlines(True),result.splitlines(True),fromfile='a/src/smf/'+name,tofile='b/src/smf/'+name))
        command = next(shlex.split(c) for c in commands if c.endswith('../src/smf/'+name))
        obj = output/Path(command[command.index('-o')+1]).name
        command[command.index('-o')+1] = str(obj)
        for flag in ('-MF','-MQ'):
            if flag in command:command[command.index(flag)+1]=str(obj)+('.d' if flag=='-MF' else '')
        command[-1] = str(output/name)
        subprocess.run(command,cwd=build,check=True)
        subprocess.run(['ar','r',str(archive),str(obj)],check=True)
    (output/'urllc-policy.patch').write_text(''.join(diff))
    link = next(shlex.split(c) for c in commands if ' -o src/smf/open5gs-smfd ' in c)
    link[link.index('-o')+1] = str(output/'open5gs-smfd')
    link[link.index('src/smf/libsmf.a')] = str(archive)
    subprocess.run(link,cwd=build,check=True)
    for path,digest in originals.items():assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
    lib = output/'lib'
    lib.mkdir()
    for arg in link:
        path = build/arg
        if path.is_file() and '.so' in path.name and not arg.startswith('/'):
            shutil.copy2(path,lib/path.name)
            short=path.name.split('.so')[0]+'.so'
            for alias in (short,short+'.2'):
                if alias!=path.name and not (lib/alias).exists(): (lib/alias).symlink_to(path.name)
    subprocess.run([str(output/'open5gs-smfd'),'-v'],cwd=build,check=True,
                   env=os.environ | {'LD_LIBRARY_PATH':str(lib)})
    print('PASS: dedicated SMF built; shared source untouched; 13 policy isolation assertions')


if __name__ == '__main__':main()
