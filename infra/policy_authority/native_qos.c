/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Explicit supported profile: authenticated Session-AMBR, same default 5QI.
 * No PDR/FAR/QER/URR creation/removal; only MBR of an existing native QER.
 * The native SMF cache is changed only after the matching accepted N4 reply.
 */
#include "context.h"
#include "native_qos.h"
#include "native_pfcp_ogs.h"
#include "native_completion.h"

static struct {
    bool active;
    uint32_t xact_id, sequence, session_id, bearer_id, qer_id;
    uint64_t token, serial, seid, uplink, downlink;
} action;

void mqa_cancel(void)
{
    ogs_pfcp_xact_t *xact;
    if (!action.active) return;
    action.active = false;
    xact = ogs_pfcp_xact_find_by_id(action.xact_id);
    if (xact && xact->xid == action.sequence) ogs_pfcp_xact_delete(xact);
}

static void timeout(ogs_pfcp_xact_t *xact, void *data)
{
    (void)data;
    /* The PFCP timer owns deletion of this transaction. */
    if (action.active && action.xact_id == xact->id && action.sequence == xact->xid) {
        action.active = false;
        mnc_ack(xact->id, xact->xid, false);
    }
}

bool mqa_update(smf_sess_t *sess, ogs_sbi_stream_t *stream, OpenAPI_sm_policy_decision_t *decision)
{
    OpenAPI_map_t *map;
    OpenAPI_session_rule_t *rule;
    smf_bearer_t *bearer = smf_default_bearer_in_sess(sess);
    ogs_pfcp_qer_t proposed;
    ogs_pfcp_message_t *message = NULL;
    ogs_pfcp_header_t header;
    ogs_pkbuf_t *packet = NULL;
    ogs_pfcp_xact_t *xact = NULL;
    uint64_t uplink, downlink;
    const char *error = "unsupported_native_session_ambr_profile";
    if (!mfc_current(sess->smf_n4_seid) || action.active || mnc_pending() ||
            !decision || !decision->sess_rules || decision->sess_rules->count != 1 ||
            (decision->pcc_rules && decision->pcc_rules->count) ||
            (decision->qos_decs && decision->qos_decs->count) ||
            !bearer || !bearer->qer || !sess->upf_n4_seid || !sess->pfcp_node) goto reject;
    map = decision->sess_rules->first->data;
    rule = map ? map->value : NULL;
    if (!rule || !rule->auth_sess_ambr || !rule->auth_sess_ambr->uplink ||
            !rule->auth_sess_ambr->downlink) goto reject;
    if (rule->auth_def_qos && rule->auth_def_qos->_5qi != sess->session.qos.index) {
        error = "default_5qi_change_requires_ran_procedure";
        goto reject;
    }
    uplink = ogs_sbi_bitrate_from_string(rule->auth_sess_ambr->uplink);
    downlink = ogs_sbi_bitrate_from_string(rule->auth_sess_ambr->downlink);
    if (uplink < 1000 || downlink < 1000 || uplink > UINT64_C(100000000000) ||
            downlink > UINT64_C(100000000000) || uplink % 1000 || downlink % 1000 ||
            uplink < bearer->qer->gbr.uplink || downlink < bearer->qer->gbr.downlink) goto reject;
    if (mnc_begin(&maestro_native_gate, sess->smf_n4_seid, ogs_sbi_id_from_stream(stream))) goto reject;
    xact = ogs_pfcp_xact_local_create(sess->pfcp_node, timeout, NULL);
    if (!xact) goto failed;
    memset(&action, 0, sizeof(action));
    action.active = true; action.xact_id = xact->id; action.sequence = xact->xid;
    action.session_id = sess->id; action.bearer_id = bearer->id; action.qer_id = bearer->qer->id;
    action.token = maestro_native_gate.token; action.serial = maestro_native_gate.serial;
    action.seid = sess->smf_n4_seid; action.uplink = uplink; action.downlink = downlink;
    xact->local_seid = sess->smf_n4_seid;
    xact->modify_flags = OGS_PFCP_MODIFY_SESSION | OGS_PFCP_MODIFY_QOS_MODIFY;
    message = ogs_calloc(1, sizeof(*message));
    if (!message) goto failed;
    proposed = *bearer->qer; proposed.mbr.uplink = uplink; proposed.mbr.downlink = downlink;
    ogs_pfcp_build_update_qer(&message->pfcp_session_modification_request.update_qer[0],
        0, &proposed, OGS_PFCP_MODIFY_QOS_MODIFY);
    message->h.type = OGS_PFCP_SESSION_MODIFICATION_REQUEST_TYPE;
    packet = ogs_pfcp_build_msg(message);
    ogs_free(message); message = NULL;
    if (!packet) goto failed;
    if (mfp_smf_tag(&packet, sess->smf_n4_seid) || mnc_track(xact->id, xact->xid)) {
        ogs_pkbuf_free(packet); packet = NULL;
        goto failed;
    }
    memset(&header, 0, sizeof(header));
    header.type = OGS_PFCP_SESSION_MODIFICATION_REQUEST_TYPE; header.seid = sess->upf_n4_seid;
    if (ogs_pfcp_xact_update_tx(xact, &header, packet) != OGS_OK) goto failed;
    packet = NULL; /* The transaction owns the built packet now. */
    if (ogs_pfcp_xact_commit(xact) != OGS_OK) goto failed;
    mnc_seal();
    return true;
failed:
    if (message) ogs_free(message);
    mqa_cancel(); mnc_cancel();
    return false;
reject:
    ogs_sbi_server_send_error(stream, OGS_SBI_HTTP_STATUS_BAD_REQUEST, NULL, error, NULL, NULL);
    return false;
}

bool mqa_response(smf_sess_t *sess, ogs_pfcp_xact_t *xact,
                  ogs_pfcp_session_modification_response_t *response)
{
    smf_bearer_t *bearer;
    uint32_t id, sequence;
    bool accepted;
    if (!action.active || action.xact_id != xact->id || action.sequence != xact->xid) return false;
    id = xact->id; sequence = xact->xid;
    bearer = sess ? smf_default_bearer_in_sess(sess) : NULL;
    accepted = sess && sess->id == action.session_id && sess->smf_n4_seid == action.seid &&
        bearer && bearer->id == action.bearer_id && bearer->qer && bearer->qer->id == action.qer_id &&
        response->cause.presence && response->cause.u8 == OGS_PFCP_CAUSE_REQUEST_ACCEPTED &&
        mf_stamp_valid(&maestro_native_gate, action.token, action.serial, action.seid);
    action.active = false;
    ogs_pfcp_xact_commit(xact);
    if (accepted) {
        bearer->qer->mbr.uplink = action.uplink;
        bearer->qer->mbr.downlink = action.downlink;
        sess->session.ambr.uplink = action.uplink;
        sess->session.ambr.downlink = action.downlink;
    }
    mnc_ack(id, sequence, accepted);
    return true;
}
