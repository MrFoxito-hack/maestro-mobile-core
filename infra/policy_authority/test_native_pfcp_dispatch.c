/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Integration test compiled with Open5GS headers and linked to libogscore.
 * Uses its actual ogs_pkbuf_t and helpers; no mock packet-buffer ABI.
 */
#define _GNU_SOURCE
#include "ogs-core.h"
#define OGS_PFCP_SESSION_MODIFICATION_REQUEST_TYPE 52
#include "native_pfcp_ogs.h"
#include <assert.h>
#include <stdio.h>
#include <unistd.h>

static uint64_t now_ns = 100;
static uint64_t clock_ns(void) { return now_ns; }
static const char *boot = "01234567-89ab-cdef-0123-456789abcdef";

int main(void)
{
    char dir[] = "/tmp/maestro-dispatch-test-XXXXXX", file[256];
    unsigned char bytes[1024], cookie[MF_COOKIE_BYTES], frame[MF_ENVELOPE_BYTES];
    char envelope[MFC_HEX_BYTES];
    /* Update QER, QER ID = 1. Native parser applies remaining semantic checks. */
    const unsigned char qer[] = {0, 14, 0, 8, 0, 109, 0, 4, 0, 0, 0, 1};
    const unsigned char urr[] = {0, 13, 0, 8, 0, 81, 0, 4, 0, 0, 0, 1};
    ogs_pkbuf_t packet = {0};
    ogs_pkbuf_t *packet_pointer = &packet;
    unsigned char *header;
    size_t i;
    assert(setenv("MAESTRO_POLICY_PFCP_ENTERPRISE", "32473", 1) == 0);
    assert(mkdtemp(dir) && mfc_open(dir, clock_ns) == 0);
    assert(mf_fence(&maestro_native_gate, 1, boot, 1000) == 0);
    memset(cookie, 0xA5, sizeof(cookie));
    assert(mf_prepare(&maestro_native_gate, 1, 0, 42, cookie) == 0);
    assert(mf_encode(&maestro_native_gate, 42, frame) == 0);
    for (i = 0; i < sizeof(frame); i++) snprintf(envelope + 2*i, 3, "%02x", frame[i]);
    assert(mfc_enter(envelope, 42) == 0);
    {
        ogs_pkbuf_config_t config;
        ogs_pkbuf_t *exact;
        ogs_core_initialize();
        ogs_pkbuf_default_init(&config);
        ogs_pkbuf_default_create(&config);
        exact = ogs_pkbuf_alloc(NULL, 16 + sizeof(qer));
        assert(exact);
        ogs_pkbuf_reserve(exact, 16);
        ogs_pkbuf_put_data(exact, qer, sizeof(qer));
        assert(ogs_pkbuf_tailroom(exact) == 0);
        assert(mfp_smf_tag(&exact, 42) == 0);
        assert(exact->len == sizeof(qer) + MFP_IE_BYTES);
        assert(ogs_pkbuf_headroom(exact) >= 16 && !memcmp(exact->data, qer, sizeof(qer)));
        ogs_pkbuf_push(exact, 16); /* xact_update_tx must still fit its header. */
        ogs_pkbuf_free(exact);
        ogs_pkbuf_default_destroy();
        ogs_core_terminate();
        /* Open5GS enables libtalloc's process-global null tracking root. */
        talloc_disable_null_tracking();
    }
    packet.head = bytes; packet.data = packet.tail = bytes + 128; packet.end = bytes + sizeof(bytes);
    ogs_pkbuf_put_data(&packet, qer, sizeof(qer));
    assert(mfp_smf_tag(&packet_pointer, 43) < 0); /* A different session cannot borrow it. */
    assert(mfp_smf_tag(&packet_pointer, 42) == 0 && packet.len == sizeof(qer) + MFP_IE_BYTES);
    header = ogs_pkbuf_push(&packet, 16);
    memset(header, 0, 16); header[0] = 0x21; header[1] = 52; header[11] = 84;
    header[2] = (packet.len - 4) >> 8; header[3] = packet.len - 4;
    /* Receiver uses its own UPF SEID, linked by the common prepared cookie. */
    mfc_leave(); mf_cancel(&maestro_native_gate);
    assert(mf_prepare(&maestro_native_gate, 1, 0, 84, cookie) == 0);
    assert(mfp_upf_receive(&packet));
    assert(packet.param[0] == 1 && packet.len == 16 + sizeof(qer));
    ogs_pkbuf_pull(&packet, 16); /* Same operation performed by the PFCP parser. */
    assert(mfp_upf_dispatch(&packet, 84) && !mfp_upf_dispatch(&packet, 85));
    assert(mf_fence(&maestro_native_gate, 2, boot, 1000) == 0);
    assert(!mfp_upf_dispatch(&packet, 84)); /* Fence between receive and dispatch. */
    packet.param[0] = packet.param[1] = 0;
    assert(!mfp_upf_dispatch(&packet, 84)); /* Untagged QER may not impersonate CHF. */
    memcpy(packet.data, urr, sizeof(urr));
    assert(mfp_upf_dispatch(&packet, 84)); /* Accounting remains on native N4. */
    now_ns += 1001;
    memcpy(packet.data, qer, sizeof(qer));
    assert(!mfp_upf_dispatch(&packet, 84)); /* Expiry never reopens policy writers. */
    assert(mf_fence(&maestro_native_gate, 3, boot, 0) == 0);
    assert(!mfp_upf_dispatch(&packet, 84)); /* Release must not reopen legacy writers. */
    mfc_close();
    assert(!mfp_upf_dispatch(&packet, 84));
    snprintf(file, sizeof(file), "%s/fence.state", dir); assert(unlink(file) == 0);
    snprintf(file, sizeof(file), "%s/fence.lock", dir); assert(unlink(file) == 0);
    assert(rmdir(dir) == 0);
    puts("PASS Open5GS N4 dispatch: real pkbuf ABI, SMF/UPF SEID correlation, token recheck after queue, expiry, native URR path");
    return 0;
}
