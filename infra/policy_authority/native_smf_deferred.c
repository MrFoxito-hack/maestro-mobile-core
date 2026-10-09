/* SPDX-License-Identifier: AGPL-3.0-or-later */
#include "native_smf_deferred.h"
#include "native_deferred.h"
#include "native_control.h"

static mdq_queue events;

bool msd_ready(void)
{
    /* Admission may be closed while previously admitted operations drain. */
    return !mfc_enabled() || (!maestro_native_gate.token && mfc_system_admit()) ||
        mf_system_ready(&maestro_native_gate);
}

static void dispose(void *data)
{
    smf_event_t *e = data;
    if (e->pkbuf) ogs_pkbuf_free(e->pkbuf);
    ogs_event_free(e);
}

static bool valid(uint64_t id, uint64_t generation, void *data)
{
    smf_sess_t *sess;
    smf_event_t *e = data;
    if (!id && e->h.id == SMF_EVT_N4_NO_HEARTBEAT) return e->pfcp_node != NULL;
    sess = smf_sess_find_by_id((ogs_pool_id_t)id);
    return sess && sess->smf_n4_seid == generation;
}

bool msd_hold(smf_event_t *e)
{
    smf_sess_t *sess;
    uint64_t id, generation;
    if (msd_ready()) return false;
    switch (e->h.id) {
    case SMF_EVT_N4_NO_HEARTBEAT:
        /* Peer reselection deletes contexts. Nodes live for the NF lifetime. */
        ogs_assert(mdq_push(&events, 0, 0, e) == 0);
        return true;
    case SMF_EVT_5GSM_MESSAGE:
    case SMF_EVT_NGAP_MESSAGE:
    case SMF_EVT_SESSION_RELEASE:
        break;
    case SMF_EVT_N4_TIMER:
        if (e->h.timer_id != SMF_TIMER_PFCP_NO_ESTABLISHMENT_RESPONSE &&
            e->h.timer_id != SMF_TIMER_PFCP_NO_DELETION_RESPONSE) return false;
        break;
    default:
        /* CHF responses and N4 usage reports MUST continue to be processed. */
        return false;
    }
    sess = smf_sess_find_by_id(e->sess_id);
    if (!sess) {
        dispose(e);
        return true;
    }
    id = sess->id;
    generation = sess->smf_n4_seid;
    /* Allocation failure follows Open5GS fail-stop semantics; never dispatch
     * an unleased writer or silently lose an accepted release intent. */
    ogs_assert(mdq_push(&events, id, generation, e) == 0);
    return true;
}

smf_event_t *msd_take(void)
{
    return mdq_take(&events, msd_ready(), valid, dispose);
}

bool msd_release(smf_sess_t *session, int trigger)
{
    smf_event_t *e;
    if (msd_ready()) return false;
    /* CHF has already consumed the response. Retain only a lifecycle intent,
     * never the transaction pointer freed by the enclosing SMF dispatcher. */
    e = smf_event_new(SMF_EVT_SESSION_RELEASE);
    ogs_assert(e);
    e->sess_id = session->id;
    e->h.sbi.state = trigger;
    ogs_assert(msd_hold(e));
    return true;
}

unsigned msd_count(void) { return (unsigned)events.count; }
void msd_forget(ogs_pool_id_t id) { mdq_forget(&events, id, dispose); }
void msd_close(void) { mdq_clear(&events, dispose); }
