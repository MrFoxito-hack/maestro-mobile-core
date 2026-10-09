/* SPDX-License-Identifier: AGPL-3.0-or-later */
#ifndef MAESTRO_NATIVE_SMF_DEFERRED_H
#define MAESTRO_NATIVE_SMF_DEFERRED_H
#include "context.h"
bool msd_ready(void);
bool msd_hold(smf_event_t *event);
bool msd_release(smf_sess_t *session, int trigger);
smf_event_t *msd_take(void);
unsigned msd_count(void);
void msd_forget(ogs_pool_id_t session_id);
void msd_close(void);
#endif
