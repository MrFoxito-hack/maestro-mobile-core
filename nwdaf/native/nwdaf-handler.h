/* SPDX-License-Identifier: AGPL-3.0-or-later */
#ifndef MAESTRO_PCF_NWDAF_HANDLER_H
#define MAESTRO_PCF_NWDAF_HANDLER_H
#include "context.h"

int pcf_nwdaf_open(void);
void pcf_nwdaf_close(void);
void *pcf_nwdaf_notify_context(pcf_sess_t *sess);
int pcf_nwdaf_notify_response(int status, ogs_sbi_response_t *response, void *data);

#endif
