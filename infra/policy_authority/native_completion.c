/* SPDX-License-Identifier: AGPL-3.0-or-later */
#include "native_completion.h"
#include <string.h>

static struct {
    mf_gate *gate;
    mnc_reply_fn reply;
    uint64_t token, serial, target;
    uint32_t stream;
    bool active, sealed, failed;
    unsigned count, waiting;
    struct { uint32_t id, sequence; bool done; } transactions[MNC_MAX_TRANSACTIONS];
} completion;

static void finish(bool accepted)
{
    mnc_reply_fn callback = completion.reply;
    uint32_t stream = completion.stream;
    if (!completion.active) return;
    completion.active = false;
    completion.waiting = 0;
    if (callback) callback(stream, accepted);
}

static bool valid(void)
{
    return completion.active && mf_stamp_valid(completion.gate,
        completion.token, completion.serial, completion.target);
}

void mnc_init(mnc_reply_fn reply)
{
    mnc_cancel();
    memset(&completion, 0, sizeof(completion));
    completion.reply = reply;
}

int mnc_begin(mf_gate *gate, uint64_t target, uint32_t stream)
{
    mnc_reply_fn callback = completion.reply;
    if (!gate || completion.active || !callback || !stream ||
        !mf_stamp_valid(gate, gate->token, gate->serial, target)) return -1;
    memset(&completion, 0, sizeof(completion));
    completion.reply = callback; completion.gate = gate; completion.stream = stream;
    completion.token = gate->token; completion.serial = gate->serial; completion.target = target;
    completion.active = true;
    return 0;
}

int mnc_track(uint32_t id, uint32_t sequence)
{
    unsigned i;
    if (!valid() || completion.sealed || completion.count >= MNC_MAX_TRANSACTIONS || !id) return -1;
    for (i = 0; i < completion.count; i++)
        if (completion.transactions[i].id == id) return -1;
    completion.transactions[completion.count].id = id;
    completion.transactions[completion.count++].sequence = sequence;
    completion.waiting++;
    return 0;
}

void mnc_seal(void)
{
    if (!completion.active) return;
    completion.sealed = true;
    if (!valid() || !completion.count) { finish(false); return; }
    if (!completion.waiting) finish(!completion.failed);
}

void mnc_ack(uint32_t id, uint32_t sequence, bool accepted)
{
    unsigned i;
    if (!completion.active) return;
    for (i = 0; i < completion.count; i++) {
        if (completion.transactions[i].id != id || completion.transactions[i].sequence != sequence) continue;
        if (completion.transactions[i].done) return;
        completion.transactions[i].done = true;
        completion.waiting--;
        if (!accepted || !valid()) completion.failed = true;
        if (completion.sealed && !completion.waiting) finish(!completion.failed && valid());
        return;
    }
}

void mnc_cancel(void) { finish(false); }
void mnc_poll(void) { if (completion.active && !valid()) finish(false); }
bool mnc_pending(void) { return completion.active; }
unsigned mnc_pending_n4(void) { return completion.active ? completion.waiting : 0; }
