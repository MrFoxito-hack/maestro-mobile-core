/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Actual SMF event/session structs and Open5GS packet buffers. Only the session
 * lookup and event allocator/free boundary are isolated from a running SMF.
 */
#define _GNU_SOURCE
#include "native_smf_deferred.h"
#include "native_control.h"
#include <assert.h>
#include <stdlib.h>
#include <unistd.h>

int __smf_log_domain;
static smf_sess_t sessions[3];
static unsigned disposed;
static uint64_t now_ns = 100;
static uint64_t clock_ns(void) { return now_ns; }
static const char *boot = "01234567-89ab-cdef-0123-456789abcdef";

smf_sess_t *smf_sess_find_by_id(ogs_pool_id_t id)
{
    return id > 0 && id < 3 && sessions[id].id ? &sessions[id] : NULL;
}
void ogs_event_free(void *e) { disposed++; free(e); }
smf_event_t *smf_event_new(int id)
{
    smf_event_t *e = calloc(1, sizeof(*e));
    assert(e); e->h.id = id; return e;
}
static smf_event_t *event(int type, unsigned id, bool packet)
{
    smf_event_t *e = calloc(1, sizeof(*e));
    assert(e);
    e->h.id = type; e->sess_id = id;
    if (packet) {
        e->pkbuf = ogs_pkbuf_alloc(NULL, 16);
        assert(e->pkbuf);
        ogs_pkbuf_put_data(e->pkbuf, "NAS-owned", 9);
    }
    return e;
}
static void destroy(smf_event_t *e)
{
    if (e->pkbuf) ogs_pkbuf_free(e->pkbuf);
    ogs_event_free(e);
}

int main(void)
{
    char dir[] = "/tmp/maestro-smf-deferred-XXXXXX", file[256];
    unsigned char cookie[MF_COOKIE_BYTES] = {1};
    ogs_pkbuf_config_t config;
    smf_event_t *a, *b, *c;
    unsigned before;
    ogs_core_initialize();
    ogs_pkbuf_default_init(&config); ogs_pkbuf_default_create(&config);
    sessions[1].id = 1; sessions[1].smf_n4_seid = 42;
    sessions[2].id = 2; sessions[2].smf_n4_seid = 43;
    assert(mkdtemp(dir) && !mfc_open(dir, clock_ns));
    a = event(SMF_EVT_5GSM_MESSAGE, 1, true);
    assert(msd_ready() && !msd_hold(a)); /* Legacy bootstrap remains usable. */
    assert(!mf_fence(&maestro_native_gate, 1, boot, 1000));
    assert(!mf_prepare(&maestro_native_gate, 1, 0, 42, cookie));
    assert(msd_hold(a) && !msd_take());
    b = event(SMF_EVT_NGAP_MESSAGE, 2, true);
    assert(msd_hold(b));
    c = event(SMF_EVT_SESSION_RELEASE, 1, false);
    assert(msd_hold(c) && msd_count() == 3);

    /* Charging responses and URR-bearing N4 messages are never owned here. */
    {
        smf_event_t accounting = {.h.id = SMF_EVT_CHF_RESPONSE};
        assert(!msd_hold(&accounting));
        accounting.h.id = SMF_EVT_N4_MESSAGE;
        assert(!msd_hold(&accounting));
        accounting.h.id = SMF_EVT_N4_TIMER;
        accounting.h.timer_id = SMF_TIMER_PFCP_NO_HEARTBEAT;
        assert(!msd_hold(&accounting));
    }
    now_ns += 1001;
    assert(!msd_take() && msd_count() == 3); /* Expiry does not reopen. */
    assert(!mf_fence(&maestro_native_gate, 2, boot, 1000));
    assert(!mf_prepare_system(&maestro_native_gate, 2, 0, cookie));
    assert(!mfc_system_admit()); /* Drain old work while admission is closed. */
    assert(msd_take() == a && !msd_hold(a));
    assert(a->pkbuf->len == 9 && !memcmp(a->pkbuf->data, "NAS-owned", 9));
    destroy(a);
    assert(msd_take() == b); destroy(b);
    assert(msd_take() == c); destroy(c);
    assert(!msd_take() && !msd_count());

    assert(!mf_fence(&maestro_native_gate, 3, boot, 1000));
    a = event(SMF_EVT_5GSM_MESSAGE, 1, true);
    b = event(SMF_EVT_NGAP_MESSAGE, 2, true);
    c = event(SMF_EVT_SESSION_RELEASE, 1, false);
    assert(msd_hold(a) && msd_hold(b) && msd_hold(c));
    before = disposed;
    msd_forget(1); /* Invalidation before the Open5GS pool slot is recycled. */
    assert(disposed == before + 2 && msd_count() == 1);
    sessions[2].smf_n4_seid = 99; /* Even a missed invalidation fails identity. */
    assert(!mf_fence(&maestro_native_gate, 4, boot, 1000));
    assert(!mf_prepare_system(&maestro_native_gate, 4, 0, cookie));
    assert(!msd_take() && disposed == before + 3);
    assert(!mf_fence(&maestro_native_gate, 5, boot, 1000));
    a = event(SMF_EVT_N4_TIMER, 1, false);
    a->h.timer_id = SMF_TIMER_PFCP_NO_DELETION_RESPONSE;
    assert(msd_hold(a) && msd_count() == 1);
    assert(msd_release(&sessions[2], OGS_PFCP_DELETE_TRIGGER_LOCAL_INITIATED));
    assert(msd_count() == 2);
    b = event(SMF_EVT_5GSM_MESSAGE, 0, true);
    before = disposed;
    assert(msd_hold(b) && disposed == before + 1 && msd_count() == 2);
    assert(!mf_fence(&maestro_native_gate, 6, boot, 1000));
    assert(!mf_prepare_system(&maestro_native_gate, 6, 0, cookie));
    assert(msd_take() == a); destroy(a);
    c = msd_take();
    assert(c && c->h.id == SMF_EVT_SESSION_RELEASE && c->sess_id == 2 &&
        c->h.sbi.state == OGS_PFCP_DELETE_TRIGGER_LOCAL_INITIATED && !c->h.sbi.data);
    destroy(c);
    assert(!msd_release(&sessions[2], OGS_PFCP_DELETE_TRIGGER_LOCAL_INITIATED));
    assert(!mf_fence(&maestro_native_gate, 7, boot, 1000));
    a = event(SMF_EVT_NGAP_MESSAGE, 1, true); assert(msd_hold(a));
    before = disposed;
    msd_close(); assert(!msd_count() && disposed == before + 1);
    mfc_close();
    snprintf(file, sizeof(file), "%s/fence.state", dir); assert(!unlink(file));
    snprintf(file, sizeof(file), "%s/fence.lock", dir); assert(!unlink(file));
    assert(!rmdir(dir));
    ogs_pkbuf_default_destroy(); ogs_core_terminate(); talloc_disable_null_tracking();
    puts("PASS SMF lifecycle: FIFO, expiry, closed-admission drain, payload ownership, pool reuse, session timers, CHF/N4 pass-through");
    return 0;
}
