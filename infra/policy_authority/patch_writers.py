"""Exact-source native writer hooks for the CHF-compatible lab build.

The supported profile is 5GC IPv4 Session-AMBR. EPC/AF/subscription writers
are rejected; SBI completions and native lifecycle events share SYSTEM fencing.
Every anchor is checked before compiling an isolated replacement object.
"""


def once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"Unexpected native source anchor: {old[:100]!r}")
    return source.replace(old, new)


def patch(nf, name, source):
    original = source
    if name.endswith('.c'):
        source = '#include "native_control.h"\n' + source
    if (nf, name) == ('pcf', 'sbi-path.c'):
        for function in ('update', 'delete'):
            start = source.index(f'bool pcf_sbi_send_smpolicycontrol_{function}_notify(')
            end = source.index('\n}', start) + 2
            body = source[start:end]
            body = once(body, '    bool rc;', '    bool rc;\n    char authority[MFC_HEX_BYTES];')
            body = once(body, '    ogs_assert(sess);', '''    ogs_assert(sess);
    if (mfc_export(sess->id, authority)) {
        ogs_warn("Native authority rejected N7 policy writer");
        return false;
    }''')
            anchor = ('    if (pcf_nwdaf_notify_context(sess))' if function == 'update'
                      else '    rc = ogs_sbi_send_request_to_client(')
            body = once(body, anchor, '''    if (authority[0])
        ogs_sbi_header_set(request->http.headers, "x-maestro-policy", authority);

''' + anchor)
            source = source[:start] + body + source[end:]
    elif (nf, name) == ('pcf', 'mml-control.inc'):
        source = once(source, '    cJSON *root, *op, *mode, *supi, *qi, *dl, *ul, *json;',
                      '    cJSON *root, *op, *mode, *supi, *qi, *dl, *ul, *json, *authority, *dnn, *pdu;')
        source = once(source, '    supi = cJSON_GetObjectItemCaseSensitive(root, "supi");', '''    dnn = cJSON_GetObjectItemCaseSensitive(root, "dnn");
    pdu = cJSON_GetObjectItemCaseSensitive(root, "pdu_id");
    if (mfc_enabled() && (!cJSON_IsString(dnn) || !dnn->valuestring[0] ||
            !cJSON_IsNumber(pdu) || pdu->valueint < 1 || pdu->valueint > 255 ||
            !isfinite(pdu->valuedouble) || fabs(pdu->valuedouble - pdu->valueint) > 0)) goto invalid;
    supi = cJSON_GetObjectItemCaseSensitive(root, "supi");''')
        source = once(source, '            if (!sess->dnn || strcmp(sess->dnn, "internet")) continue;', '''            if (mfc_enabled()) {
                if (!sess->dnn || strcmp(sess->dnn, dnn->valuestring) || sess->psi != pdu->valueint) continue;
            } else if (!sess->dnn || strcmp(sess->dnn, "internet")) continue;''')
        source = once(source, '    if (!strcmp(op->valuestring, "mode")) {', '''    authority = cJSON_GetObjectItemCaseSensitive(root, "authority");
    if (!strcmp(op->valuestring, "mode")) {
        if (mfc_enter(cJSON_IsString(authority) ? authority->valuestring : NULL, 0)) {
            mml_reply(&peer, peer_len, false, "native_authority_rejected");
            goto done;
        }''')
        source = once(source, '    if (dl) snprintf(dl_field', '''    if (mfc_enter(cJSON_IsString(authority) ? authority->valuestring : NULL, selected->id)) {
        mml_reply(&peer, peer_len, false, "native_authority_rejected");
        goto done;
    }
    if (dl) snprintf(dl_field''')
        source = once(source, '    tx->mml = true;', '''    tx->mml = true;
    if (mfc_enabled()) {
        tx->authority_token = maestro_native_gate.token;
        tx->authority_serial = maestro_native_gate.serial;
        tx->authority_policy.uplink = (uint64_t)(ul->valuedouble * 1000000.0 + 0.5);
        tx->authority_policy.downlink = (uint64_t)(dl->valuedouble * 1000000.0 + 0.5);
        tx->authority_policy.five_qi = qi->valueint;
    }''')
        source = once(source, '    json = cJSON_Parse(document);', '''    if (mfc_enabled()) {
        if (!dl || !ul) {
            mml_reply(&peer, peer_len, false, "native_ambr_requires_both_observed_directions");
            goto done;
        }
        /* Existing QER only: never introduce a new PDR/URR binding to change MBR. */
        snprintf(document, sizeof(document),
            "{\\"sessRules\\":{\\"maestro-ambr\\":{\\"sessRuleId\\":\\"maestro-ambr\\","
            "\\"authSessAmbr\\":{\\"uplink\\":\\"%.6f Mbps\\",\\"downlink\\":\\"%.6f Mbps\\"},"
            "\\"authDefQos\\":{\\"5qi\\":%d,\\"arp\\":{\\"priorityLevel\\":8,"
            "\\"preemptCap\\":\\"NOT_PREEMPT\\",\\"preemptVuln\\":\\"NOT_PREEMPTABLE\\"}}}}}",
            ul->valuedouble, dl->valuedouble, qi->valueint);
    }
    json = cJSON_Parse(document);''')
        source = once(source, 'done:\n    cJSON_Delete(root);', 'done:\n    mfc_leave();\n    cJSON_Delete(root);')
    elif (nf, name) == ('pcf', 'nwdaf-handler.c'):
        source = once(source, '#include "context.h"', '#include "context.h"\n#include "native_pcf.h"')
        source = once(source, '    bool mml;', '    bool mml;\n    uint64_t authority_token, authority_serial;\n    mpc_policy authority_policy;')
        anchor = 'static pcf_nwdaf_context_t nwdaf;'
        source = once(source, anchor, anchor + '''
typedef struct mpc_override {
    struct mpc_override *next;
    uint32_t session_id;
    mpc_policy policy;
} mpc_override;
static mpc_override *mpc_overrides;
bool mpc_mode_autonomous(void) { return nwdaf.enabled; }
void mpc_policy_remove(uint32_t id)
{
    mpc_override **entry = &mpc_overrides;
    while (*entry) {
        if ((*entry)->session_id == id) {
            mpc_override *old = *entry;
            *entry = old->next; ogs_free(old); return;
        }
        entry = &(*entry)->next;
    }
}
static void mpc_policy_set(uint32_t id, const mpc_policy *policy)
{
    mpc_override *entry;
    mpc_policy_remove(id);
    entry = ogs_calloc(1, sizeof(*entry));
    ogs_assert(entry);
    entry->session_id = id; entry->policy = *policy;
    entry->next = mpc_overrides; mpc_overrides = entry;
}
bool mpc_policy_get(pcf_sess_t *sess, mpc_policy *policy)
{
    mpc_override *entry;
    if (!sess || !policy || sess->nwdaf_pending || sess->nwdaf_throttled ||
            ogs_list_count(&sess->app_list)) return false;
    for (entry = mpc_overrides; entry; entry = entry->next)
        if (entry->session_id == sess->id) { *policy = entry->policy; return true; }
    if (!sess->sm_policy_dnn_data || !sess->subscribed_sess_ambr ||
            !sess->subscribed_default_qos || !sess->subscribed_sess_ambr->uplink ||
            !sess->subscribed_sess_ambr->downlink) return false;
    policy->uplink = ogs_sbi_bitrate_from_string(sess->subscribed_sess_ambr->uplink);
    policy->downlink = ogs_sbi_bitrate_from_string(sess->subscribed_sess_ambr->downlink);
    policy->five_qi = sess->subscribed_default_qos->_5qi;
    return policy->uplink && policy->downlink && policy->five_qi;
}
''')
        source = once(source, '    bool accepted = status == OGS_OK && response && response->status == 204;', '''    bool accepted = status == OGS_OK && response && response->status == 204;
    if (mfc_enabled() && !mf_stamp_valid(&maestro_native_gate,
            tx->authority_token, tx->authority_serial, tx->sess_id))
        accepted = false;''')
        source = once(source, '    if (sess->nwdaf_pending || sess->nwdaf_throttled == throttle ||', '''    /* Autonomous analytics must acquire the same authority as MML first. */
    if (!mfc_current(sess->id)) return;
    if (sess->nwdaf_pending || sess->nwdaf_throttled == throttle ||''')
        source = once(source, '    if (tx->mml)\n        mml_reply', '''    if (accepted && sess && tx->mml && mfc_enabled())
        mpc_policy_set(sess->id, &tx->authority_policy);
    if (tx->mml)
        mml_reply''')
    elif (nf, name) == ('pcf', 'context.c'):
        source = '#include "context.h"\n#include "native_pcf.h"\n' + source
        start = source.index('void pcf_sess_remove(pcf_sess_t *sess)')
        end = source.index('\n}', start) + 2
        body = once(source[start:end], '    ogs_assert(sess);', '    ogs_assert(sess);\n    mpc_policy_remove(sess->id);')
        source = source[:start] + body + source[end:]
    elif (nf, name) == ('pcf', 'pcf-sm.c'):
        anchor = '        case OpenAPI_service_name_npcf_smpolicycontrol:'
        start = source.index('    case OGS_EVENT_SBI_SERVER:')
        end = source.index('    case OGS_EVENT_SBI_CLIENT:', start)
        body = source[start:end]
        body = once(body, anchor, anchor + '''
            if (!mfc_system_admit()) {
                ogs_sbi_server_send_error(stream,
                    OGS_SBI_HTTP_STATUS_SERVICE_UNAVAILABLE, &message,
                    "native_lifecycle_lease_unavailable", NULL, NULL);
                break;
            }''')
        source = source[:start] + body + source[end:]
        anchor = '        case OpenAPI_service_name_npcf_policyauthorization:'
        source = once(source, anchor, anchor + '''
            /* Stop AF writers before any app/session cache is modified.
             * An authorized AF adapter is not implemented yet. */
            if (mfc_enabled()) {
                ogs_sbi_server_send_error(stream,
                    OGS_SBI_HTTP_STATUS_SERVICE_UNAVAILABLE, &message,
                    "native_authority_required", NULL, NULL);
                break;
            }''')
    elif (nf, name) == ('smf', 'smf-sm.c'):
        for event in ('SMF_EVT_S5C_MESSAGE', 'SMF_EVT_GN_MESSAGE'):
            anchor = '    case ' + event + ':'
            source = once(source, anchor, anchor + '''
        /* EPC is outside the fenced 5GC profile. Reject before creating or
         * finding contexts; Diameter therefore has no admitted EPC session. */
        if (mfc_enabled()) {
            if (e->pkbuf) ogs_pkbuf_free(e->pkbuf);
            ogs_warn("native_coverage_rejects_epc");
            break;
        }''')
        start = source.index('    case OGS_EVENT_SBI_SERVER:')
        end = source.index('    case OGS_EVENT_SBI_CLIENT:', start)
        body = source[start:end]
        anchor = '        case OpenAPI_service_name_nsmf_pdusession:'
        body = once(body, anchor, anchor + '''
            if (!mfc_system_admit()) {
                ogs_sbi_server_send_error(stream,
                    OGS_SBI_HTTP_STATUS_SERVICE_UNAVAILABLE, &sbi_message,
                    "native_lifecycle_lease_unavailable", NULL, NULL);
                break;
            }''')
        source = source[:start] + body + source[end:]
        anchor = '                SWITCH(sbi_message.h.resource.component[2])\n                CASE(OGS_SBI_RESOURCE_NAME_UPDATE)'
        source = once(source, anchor, '''                if (mfc_enter(ogs_sbi_header_get(sbi_request->http.headers,
                            "x-maestro-policy"), sess->smf_n4_seid)) {
                    ogs_sbi_server_send_error(stream,
                        OGS_SBI_HTTP_STATUS_SERVICE_UNAVAILABLE, &sbi_message,
                        "native_authority_rejected", NULL, NULL);
                    break;
                }
''' + anchor)
        anchor = '                END\n                break;\n            CASE(OGS_SBI_RESOURCE_NAME_SDMSUBSCRIPTION_NOTIFY)'
        source = once(source, anchor, '                END\n                mfc_leave();\n                break;\n            CASE(OGS_SBI_RESOURCE_NAME_SDMSUBSCRIPTION_NOTIFY)')
        anchor = '            CASE(OGS_SBI_RESOURCE_NAME_SDMSUBSCRIPTION_NOTIFY)'
        source = once(source, anchor, anchor + '''
                if (mfc_enabled()) {
                    ogs_sbi_server_send_error(stream,
                        OGS_SBI_HTTP_STATUS_SERVICE_UNAVAILABLE, &sbi_message,
                        "native_subscription_policy_adapter_required", NULL, NULL);
                    break;
                }''')
    elif (nf, name) == ('smf', 'gsm-sm.c'):
        source = '#include "native_smf_deferred.h"\n' + source
        start = source.index('        /* Nchf Update/Release response while session is operational. */')
        end = source.index('\n    case OGS_EVENT_SBI_SERVER:', start)
        body = source[start:end]
        transition = 'OGS_FSM_TRAN(s, smf_gsm_state_wait_pfcp_deletion);'
        if body.count(transition) != 3:
            raise ValueError('Unexpected operational CHF release paths')
        body = body.replace(transition,
            'if (!msd_release(sess, OGS_PFCP_DELETE_TRIGGER_LOCAL_INITIATED))\n'
            '                    ' + transition)
        source = source[:start] + body + source[end:]
        # Usage reports must be accounted immediately, but their release
        # consequence is a lifecycle writer just like an Nchf response.
        # Do not retain the report/xact: msd_release queues only an intent.
        source = once(source, '''            if (pfcp_cause != OGS_PFCP_CAUSE_REQUEST_ACCEPTED) {
                e->h.sbi.state = OGS_PFCP_DELETE_TRIGGER_LOCAL_INITIATED;
                OGS_FSM_TRAN(s, smf_gsm_state_wait_pfcp_deletion);
            }''', '''            if (pfcp_cause != OGS_PFCP_CAUSE_REQUEST_ACCEPTED &&
                    !msd_release(sess, OGS_PFCP_DELETE_TRIGGER_LOCAL_INITIATED)) {
                e->h.sbi.state = OGS_PFCP_DELETE_TRIGGER_LOCAL_INITIATED;
                OGS_FSM_TRAN(s, smf_gsm_state_wait_pfcp_deletion);
            }''')
    elif (nf, name) == ('smf', 'context.c'):
        source = '#include "native_smf_deferred.h"\n' + source
        start = source.index('void smf_sess_remove(smf_sess_t *sess)\n{')
        end = source.index('\n}', start) + 2
        body = once(source[start:end], '    ogs_assert(sess);',
                    '    ogs_assert(sess);\n    msd_forget(sess->id);')
        source = source[:start] + body + source[end:]
    elif (nf, name) == ('smf', 'pfcp-sm.c'):
        source = '#include "native_smf_deferred.h"\n' + source
        if source.count('node->restoration_required = false;') != 3:
            raise ValueError('Unexpected SMF restoration call sites')
        source = source.replace('node->restoration_required = false;',
                                '/* Cleared only by an authorized restoration. */')
        start = source.index('static void pfcp_restoration(ogs_pfcp_node_t *node)\n{')
        end = source.index('\n}', start) + 2
        anchor = '    ogs_list_for_each(&smf_self()->smf_ue_list, smf_ue) {'
        body = once(source[start:end], anchor, '''    if (!msd_ready()) return;
    node->restoration_required = false;
''' + anchor)
        source = source[:start] + body + source[end:]
        anchor = '''            SMF_SESS_CLEAR(sess);
            break;
        default:
            ogs_error("Unknown timer[%s:%d]",'''
        if source.count(anchor) != 2:
            raise ValueError('Unexpected PFCP deletion timeout handlers')
        source = source.replace(anchor, '''            if (mfc_enabled()) {
                /* A timeout is not a deletion acknowledgement. Retain the
                 * context/accounting owner; retry using the current lease. */
                ogs_warn("native_delete_unconfirmed_retaining_session=%u", sess->id);
                ogs_assert(msd_ready());
                ogs_assert(OGS_OK == smf_5gc_pfcp_send_session_deletion_request(
                    sess, NULL, e->h.sbi.state));
            } else {
                SMF_SESS_CLEAR(sess);
            }
            break;
        default:
            ogs_error("Unknown timer[%s:%d]",''')
    elif (nf, name) == ('smf', 'pfcp-path.c'):
        source = '#include "context.h"\n#include "native_pfcp_ogs.h"\n#include "native_completion.h"\n' + source
        start = source.index('int smf_pfcp_send_modify_list(')
        end = source.index('\n}', start) + 2
        body = source[start:end]
        anchor = '    rv = ogs_pfcp_xact_update_tx(xact, &h, n4buf);'
        body = once(body, anchor, '''    if (mfp_smf_tag(&n4buf, sess->smf_n4_seid)) {
        ogs_warn("Native authority rejected outgoing N4 policy writer");
        ogs_pkbuf_free(n4buf);
        ogs_pfcp_xact_delete(xact);
        mnc_cancel();
        return OGS_ERROR;
    }
    if (mfc_enabled() && mfc_current(sess->smf_n4_seid) && mnc_pending() &&
            mnc_track(xact->id, xact->xid)) {
        ogs_pkbuf_free(n4buf);
        ogs_pfcp_xact_delete(xact);
        mnc_cancel();
        return OGS_ERROR;
    }
''' + anchor)
        source = source[:start] + body + source[end:]
        for function in ('smf_5gc_pfcp_send_session_establishment_request',
                         'smf_5gc_pfcp_send_session_deletion_request',
                         'smf_epc_pfcp_send_session_establishment_request',
                         'smf_epc_pfcp_send_session_deletion_request'):
            start = source.index('int ' + function + '(')
            end = source.index('\n}', start) + 2
            body = source[start:end]
            anchor = '    rv = ogs_pfcp_xact_update_tx(xact, &h, n4buf);'
            body = once(body, anchor, '''    if (mfp_smf_tag(&n4buf, sess->smf_n4_seid)) {
        ogs_pkbuf_free(n4buf);
        ogs_pfcp_xact_delete(xact);
        return OGS_ERROR;
    }
''' + anchor)
            source = source[:start] + body + source[end:]
        for function in ('sess_5gc_timeout', 'qos_flow_5gc_timeout'):
            start = source.index('static void ' + function + '(')
            end = source.index('\n}', start) + 2
            body = source[start:end]
            anchor = '    type = xact->seq[0].type;'
            body = once(body, anchor, anchor + '\n    mnc_ack(xact->id, xact->xid, false);')
            source = source[:start] + body + source[end:]
        source = once(source,
            '        e->h.timer_id = SMF_TIMER_PFCP_NO_DELETION_RESPONSE;',
            '        e->h.timer_id = SMF_TIMER_PFCP_NO_DELETION_RESPONSE;\n'
            '        e->h.sbi.state = trigger;')
    elif (nf, name) == ('smf', 'npcf-handler.c'):
        source = '#include "context.h"\n#include "native_qos.h"\n' + source
        start = source.index('bool smf_npcf_smpolicycontrol_handle_update_notify(')
        end = source.index('\n}', start) + 2
        body = source[start:end]
        anchor = '    /* Update authorized PCC rule & QoS */'
        body = once(body, anchor, '''    if (mfc_enabled()) return mqa_update(sess, stream, SmPolicyDecision);
''' + anchor)
        source = source[:start] + body + source[end:]
    elif (nf, name) == ('smf', 'n4-handler.c'):
        source = '#include "context.h"\n#include "native_qos.h"\n#include "native_completion.h"\n' + source
        start = source.index('void smf_5gc_n4_handle_session_modification_response(')
        end = source.index('\n}', start) + 2
        body = source[start:end]
        body = once(body, '    int r, status = 0;', '    int r, status = 0;\n    uint32_t authority_xact_id, authority_sequence;')
        body = once(body, '    ogs_assert(rsp);',
                    '    ogs_assert(rsp);\n    if (mqa_response(sess, xact, rsp)) return;')
        body = once(body, '    ogs_pfcp_xact_commit(xact);', '''    authority_xact_id = xact->id;
    authority_sequence = xact->xid;
    ogs_pfcp_xact_commit(xact);''')
        anchor = '    if (status != OGS_SBI_HTTP_STATUS_OK) {'
        body = once(body, anchor, anchor + '\n        mnc_ack(authority_xact_id, authority_sequence, false);')
        anchor = '    if (sess->local_ul_addr == NULL && sess->local_ul_addr6 == NULL) {'
        body = once(body, anchor, anchor + '\n        mnc_ack(authority_xact_id, authority_sequence, false);')
        anchor = '    if (flags & OGS_PFCP_MODIFY_HOME_ROUTED_ROAMING) {'
        body = once(body, anchor, '    mnc_ack(authority_xact_id, authority_sequence, true);\n\n' + anchor)
        source = source[:start] + body + source[end:]
    elif (nf, name) == ('upf', 'pfcp-path.c'):
        source = once(source, '#include "context.h"', '#include "context.h"\n#include "native_pfcp_ogs.h"')
        anchor = '    e = upf_event_new(UPF_EVT_N4_MESSAGE);'
        source = once(source, anchor, '''    if (!mfp_upf_receive(pkbuf)) {
        ogs_warn("Native authority rejected N4 envelope");
        ogs_pkbuf_free(pkbuf);
        return;
    }

''' + anchor)
    elif (nf, name) == ('upf', 'pfcp-sm.c'):
        # context.h is included indirectly by pfcp-sm.h in this translation unit.
        anchor = 'void upf_pfcp_state_initial('
        position = source.index(anchor)
        source = source[:position] + '#include "native_pfcp_ogs.h"\n\n' + source[position:]
        # Peer restart is a rule-deleting writer too. Keep the pending flag
        # until a system lease allows restoration; later heartbeats retry it.
        if source.count('node->restoration_required = false;') != 3:
            raise ValueError('Unexpected UPF restoration call sites')
        source = source.replace('node->restoration_required = false;',
                                '/* Cleared only by an authorized restoration. */')
        start = source.index('static void pfcp_restoration(ogs_pfcp_node_t *node)\n{')
        end = source.index('\n}', start) + 2
        body = source[start:end]
        anchor = '    ogs_list_for_each_safe(&upf_self()->sess_list, next, sess) {'
        body = once(body, anchor, '''    if (mfc_enabled() && !mf_system_ready(&maestro_native_gate))
        return;
    node->restoration_required = false;
''' + anchor)
        source = source[:start] + body + source[end:]
        anchor = '        case OGS_PFCP_SESSION_MODIFICATION_REQUEST_TYPE:'
        source = once(source, anchor, anchor + '''
            if (!mfp_upf_dispatch(e->pkbuf, message->h.seid)) {
                ogs_pfcp_send_error_message(xact,
                    sess ? sess->smf_n4_f_seid.seid : 0,
                    OGS_PFCP_SESSION_MODIFICATION_RESPONSE_TYPE,
                    OGS_PFCP_CAUSE_REQUEST_REJECTED, 0);
                break;
            }''')
        for operation in ('ESTABLISHMENT', 'DELETION'):
            anchor = f'        case OGS_PFCP_SESSION_{operation}_REQUEST_TYPE:'
            source = once(source, anchor, anchor + f'''
            if (!mfp_upf_lifecycle(e->pkbuf, message->h.seid)) {{
                ogs_pfcp_send_error_message(xact,
                    sess ? sess->smf_n4_f_seid.seid : 0,
                    OGS_PFCP_SESSION_{operation}_RESPONSE_TYPE,
                    OGS_PFCP_CAUSE_REQUEST_REJECTED, 0);
                break;
            }}''')
    else:
        raise ValueError(f'No patch for {nf}/{name}')
    if source == original:
        raise ValueError(f'Empty patch for {nf}/{name}')
    return source


SOURCES = {
    'pcf': ('mml-control.inc', 'sbi-path.c', 'nwdaf-handler.c', 'pcf-sm.c', 'context.c'),
    'smf': ('smf-sm.c', 'pfcp-path.c', 'npcf-handler.c', 'n4-handler.c', 'context.c', 'pfcp-sm.c', 'gsm-sm.c'),
    'upf': ('pfcp-path.c', 'pfcp-sm.c'),
}
