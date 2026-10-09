/* SPDX-License-Identifier: AGPL-3.0-or-later */
#ifndef MAESTRO_NATIVE_COMPLETION_H
#define MAESTRO_NATIVE_COMPLETION_H
#include "native_fence.h"
#define MNC_MAX_TRANSACTIONS 64
typedef void (*mnc_reply_fn)(uint32_t stream, bool accepted);
/* One prepared authority action per NF; multiple N4 transactions may belong
 * to it. The HTTP callback is invoked once, only after seal + all N4 replies.
 */
void mnc_init(mnc_reply_fn reply);
int mnc_begin(mf_gate *gate, uint64_t target, uint32_t stream);
int mnc_track(uint32_t id, uint32_t wire_sequence);
void mnc_seal(void);
void mnc_ack(uint32_t id, uint32_t wire_sequence, bool accepted);
void mnc_cancel(void);
void mnc_poll(void);
bool mnc_pending(void);
unsigned mnc_pending_n4(void);
#endif
