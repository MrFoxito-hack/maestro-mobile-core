/* SPDX-License-Identifier: AGPL-3.0-or-later
 * PCF event-thread observation of acknowledged Session-AMBR.
 */
#ifndef MAESTRO_NATIVE_PCF_H
#define MAESTRO_NATIVE_PCF_H
#include <stdbool.h>
#include <stdint.h>
typedef struct { uint64_t uplink, downlink; unsigned five_qi; } mpc_policy;
bool mpc_policy_get(pcf_sess_t *sess, mpc_policy *policy);
void mpc_policy_remove(uint32_t session_id);
bool mpc_mode_autonomous(void);
#endif
