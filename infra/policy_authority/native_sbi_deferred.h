/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Session SBI completions/timers may outlive a SYSTEM lease. Own the original
 * event until SYSTEM is ready again; never retain a parsed stack message.
 * NRF events carry pointers, not transaction IDs, and continue uninterrupted.
 */
#ifndef MAESTRO_NATIVE_SBI_DEFERRED_H
#define MAESTRO_NATIVE_SBI_DEFERRED_H
#include "native_deferred.h"
#include "native_control.h"

static mdq_queue nsd_events;
static bool nsd_ready(void)
{
    return !mfc_enabled() || (!maestro_native_gate.token && mfc_system_admit()) ||
        mf_system_ready(&maestro_native_gate);
}
static void nsd_dispose(void *data)
{
    ogs_event_t *e = data;
    if (e->id == OGS_EVENT_SBI_CLIENT && e->sbi.response)
        ogs_sbi_response_free(e->sbi.response);
    ogs_event_free(e);
}
static bool nsd_valid(uint64_t id, uint64_t object, void *data)
{
    ogs_sbi_xact_t *x = ogs_sbi_xact_find_by_id((ogs_pool_id_t)id);
    (void)data;
    return x && x->sbi_object_id == object;
}
static bool nsd_hold(void *data)
{
    ogs_event_t *e = data;
    uintptr_t id;
    ogs_sbi_xact_t *x;
    if (nsd_ready()) return false;
    if (e->id != OGS_EVENT_SBI_CLIENT &&
            !(e->id == OGS_EVENT_SBI_TIMER && e->timer_id == OGS_TIMER_SBI_CLIENT_WAIT))
        return false;
    id = (uintptr_t)e->sbi.data;
    if (id < OGS_MIN_POOL_ID || id > OGS_MAX_POOL_ID) return false;
    x = ogs_sbi_xact_find_by_id((ogs_pool_id_t)id);
    if (!x) return false; /* Original dispatcher rejects obsolete IDs. */
    ogs_assert(mdq_push(&nsd_events, id, x->sbi_object_id, e) == 0);
    return true;
}
static void *nsd_take(void)
{
    return mdq_take(&nsd_events, nsd_ready(), nsd_valid, nsd_dispose);
}
static unsigned nsd_count(void) { return (unsigned)nsd_events.count; }
static void nsd_close(void) { mdq_clear(&nsd_events, nsd_dispose); }
#endif
