/* SPDX-License-Identifier: AGPL-3.0-or-later */
#ifndef MAESTRO_C4_BRIDGE_H
#define MAESTRO_C4_BRIDGE_H
void maestro_c4_open(void);
void maestro_c4_close(void);
void maestro_c4_before(upf_sess_t *s);
void maestro_c4_after(upf_sess_t *s);
void maestro_c4_forget(upf_sess_t *s);
void maestro_c4_import(upf_sess_t *s);
bool maestro_c4_native_allow(upf_sess_t *s, ogs_pfcp_pdr_t *p, size_t bytes, bool uplink);
bool maestro_c4_managed(upf_sess_t *s, ogs_pfcp_pdr_t *p);
void maestro_c4_metadata(FILE *f, upf_sess_t *s);
uint64_t maestro_c4_instance(void);
void upf_sess_urr_acc_add_many(upf_sess_t *s, ogs_pfcp_urr_t *urr,
        uint64_t bytes, uint64_t packets, bool uplink);
#endif
