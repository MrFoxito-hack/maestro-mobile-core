/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Include after an Open5GS NF's context.h. All calls are on its event thread.
 */
#ifndef MAESTRO_NATIVE_PFCP_OGS_H
#define MAESTRO_NATIVE_PFCP_OGS_H
#include "native_control.h"
#include "native_pfcp.h"
#include <stdlib.h>
#include <errno.h>

static inline uint16_t mfp_enterprise(void)
{
    const char *value = getenv("MAESTRO_POLICY_PFCP_ENTERPRISE");
    char *end;
    unsigned long result;
    if (!value || !*value || strspn(value, "0123456789") != strlen(value)) return 0;
    errno = 0; result = strtoul(value, &end, 10);
    return !errno && !*end && result > 0 && result <= 65535 && result != 10415 ? result : 0;
}

static inline bool mfp_holding(void)
{
    /* After adoption, absence/expiry of a lease never opens legacy writers.
     * A distinct authority-issued system grant carries normal PDU lifecycle.
     */
    return maestro_native_gate.failed || maestro_native_gate.recovery_required || maestro_native_gate.token != 0;
}

static inline int mfp_smf_tag(ogs_pkbuf_t **packet, uint64_t local_seid)
{
    ogs_pkbuf_t *pkbuf = *packet, *grown = NULL;
    unsigned char envelope[MF_ENVELOPE_BYTES];
    size_t length;
    if (!mfc_enabled()) return 0;
    if (!mfc_current(local_seid) && !mf_system_ready(&maestro_native_gate))
        return mfp_holding() && !mfp_urr_only(pkbuf->data, pkbuf->len) ? -1 : 0;
    if (mf_encode(&maestro_native_gate, local_seid, envelope)) return -1;
    /* build_msg may allocate an exact-size payload. Preserve header headroom
     * while adding space for the authority IE. Replace only on success. */
    if (ogs_pkbuf_tailroom(pkbuf) < MFP_IE_BYTES) {
        grown = ogs_pkbuf_alloc(NULL, 16 + pkbuf->len + MFP_IE_BYTES);
        if (!grown) return -1;
        ogs_pkbuf_reserve(grown, 16);
        ogs_pkbuf_put_data(grown, pkbuf->data, pkbuf->len);
        grown->param[0] = pkbuf->param[0]; grown->param[1] = pkbuf->param[1];
        pkbuf = grown;
    }
    length = pkbuf->len;
    if (mfp_append(pkbuf->data, &length, pkbuf->len + ogs_pkbuf_tailroom(pkbuf),
                mfp_enterprise(), envelope)) {
        if (grown) ogs_pkbuf_free(grown);
        return -1;
    }
    /* append() already wrote the bytes; advance only the Open5GS tail. */
    ogs_pkbuf_put(pkbuf, length - pkbuf->len);
    if (grown) { ogs_pkbuf_free(*packet); *packet = grown; }
    return 0;
}

static inline bool mfp_upf_receive(ogs_pkbuf_t *pkbuf)
{
    unsigned char envelope[MF_ENVELOPE_BYTES];
    uint64_t target;
    size_t length = pkbuf->len;
    int rc;
    pkbuf->param[0] = pkbuf->param[1] = 0;
    if (!mfc_enabled()) return true;
    rc = mfp_extract(pkbuf->data, &length, mfp_enterprise(), envelope, &target);
    if (rc < 0) return false;
    if (rc) {
        bool lifecycle = pkbuf->data[1] == 50 || pkbuf->data[1] == 54;
        if ((pkbuf->data[1] != OGS_PFCP_SESSION_MODIFICATION_REQUEST_TYPE &&
                    !(lifecycle && maestro_native_gate.system)) ||
            mf_decode(&maestro_native_gate, target, envelope, sizeof(envelope),
                &pkbuf->param[0], &pkbuf->param[1])) return false;
        ogs_pkbuf_trim(pkbuf, length);
    }
    return true;
}

static inline bool mfp_upf_lifecycle(const ogs_pkbuf_t *pkbuf, uint64_t seid)
{
    if (!mfc_enabled() || !mfp_holding()) return true;
    return maestro_native_gate.system && pkbuf->param[0] &&
        mf_stamp_valid(&maestro_native_gate, pkbuf->param[0], pkbuf->param[1], seid);
}

static inline bool mfp_upf_dispatch(const ogs_pkbuf_t *pkbuf, uint64_t seid)
{
    if (!mfc_enabled()) return true;
    if (pkbuf->param[0])
        return mf_stamp_valid(&maestro_native_gate, pkbuf->param[0], pkbuf->param[1], seid);
    return !mfp_holding() || mfp_urr_only(pkbuf->data, pkbuf->len);
}
#endif
