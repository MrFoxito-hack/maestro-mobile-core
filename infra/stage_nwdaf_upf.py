"""Stage per-QER downlink policing and rebuild; never install live binaries.

The context change alters libpfcp ABI: all consumers must be rebuilt and a
deployment must use matching libraries. Backups are unique and retained.
"""
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
sys.path.insert(0, str(ROOT/'infra/charging'))
from e2e_native import Lab
from app.core.config import get_settings

BASE = '/home/emsadmin/maestro-charging/open5gs'


def main():
    settings = get_settings()
    host = Lab(settings, settings.ssh_port)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    try:
        context_path = BASE+'/lib/pfcp/context.h'
        path_path = BASE+'/src/upf/gtp-path.c'
        context = host.read(context_path).decode()
        path = host.read(path_path).decode()
        if 'maestro_dl_bucket' in context or 'maestro_qer_allow' in path:
            raise RuntimeError('Policer already staged; inspect instead of overwriting')
        include = '#define OGS_PFCP_CONTEXT_H'
        field = '    ogs_pfcp_bitrate_t      gbr;'
        hook = '    if (!upf_quota_allow(sess, pdr, false)) goto cleanup;'
        if context.count(include) != 1 or context.count(field) != 1 or path.count(hook) != 1:
            raise RuntimeError('Source anchors changed; no source written')
        updated_context = context.replace(include, include+'\n\n#include "qer-policer.h"')
        updated_context = updated_context.replace(field, field+'''

    /* Per-QER state: shared PDRs consume one DL budget, freed with QER. */
    maestro_qer_bucket_t maestro_dl_bucket;''')
        updated_path = path.replace(hook, hook+'''
    /* N6 unicast DL policing, before URR charging. PFCP parser uses bps.
     * No changes to QFI, AF filters, GBR or subscriber quota semantics. */
    if (pdr->qer && !maestro_qer_allow(&pdr->qer->maestro_dl_bucket,
                pdr->qer->mbr.downlink,
                (uint64_t)ogs_get_monotonic_time(), recvbuf->len))
        goto cleanup;''')
        # Validate everything first; preserve unrelated pre-existing patches.
        for name, original in [(context_path, context), (path_path, path)]:
            host.write(name+'.pre-nwdaf-policer-'+stamp, original.encode())
        host.write(BASE+'/lib/pfcp/qer-policer.h',
                   (ROOT/'nwdaf/native/qer-policer.h').read_bytes(), mode=0o644)
        with host.client.open_sftp() as sftp:
            for name, content in [(context_path, updated_context), (path_path, updated_path)]:
                with sftp.open(name, 'w') as output:
                    output.write(content.encode())
        print('Staged with backup suffix: '+stamp, flush=True)
        print(host.run(['ninja', '-C', BASE+'/build'], timeout=900), flush=True)
        print('Rebuilt matching consumers. NOT installed; live services unchanged.')
    finally:
        host.client.close()


if __name__ == '__main__':
    main()
