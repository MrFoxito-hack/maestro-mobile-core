/* SPDX-License-Identifier: AGPL-3.0-or-later
 * SMF-only, include after context.h. Session-AMBR update on the existing QER.
 */
#ifndef MAESTRO_NATIVE_QOS_H
#define MAESTRO_NATIVE_QOS_H
bool mqa_update(smf_sess_t *sess, ogs_sbi_stream_t *stream,
                OpenAPI_sm_policy_decision_t *decision);
bool mqa_response(smf_sess_t *sess, ogs_pfcp_xact_t *xact,
                  ogs_pfcp_session_modification_response_t *response);
void mqa_cancel(void);
#endif
