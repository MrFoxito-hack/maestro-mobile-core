/* SPDX-License-Identifier: AGPL-3.0-or-later */
#define _GNU_SOURCE
#include "native_completion.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static uint64_t now_ns = 100;
static uint64_t clock_ns(void) { return now_ns; }
static unsigned replies, successes;
static void reply(uint32_t stream, bool accepted)
{
    assert(stream == 7);
    assert(!mnc_pending());
    replies++; successes += accepted;
}

int main(void)
{
    mf_gate gate;
    char dir[] = "/tmp/maestro-completion-test-XXXXXX", file[256];
    unsigned char cookie[MF_COOKIE_BYTES];
    const char *boot = "01234567-89ab-cdef-0123-456789abcdef";
    unsigned i;
    assert(mkdtemp(dir)); memset(cookie, 0xA5, sizeof(cookie));
    assert(mf_open(&gate, dir, clock_ns) == 0);
    assert(mf_fence(&gate, 1, boot, 1000) == 0);
    assert(mf_prepare(&gate, 1, 0, 42, cookie) == 0);
    mnc_init(reply);
    assert(mnc_begin(NULL, 42, 7) < 0);
    assert(mnc_begin(&gate, 43, 7) < 0);
    assert(mnc_begin(&gate, 42, 7) == 0);
    assert(mnc_begin(&gate, 42, 7) < 0);
    assert(mnc_track(1, 21) == 0 && mnc_track(2, 22) == 0);
    assert(mnc_track(1, 21) < 0);
    mnc_ack(1, 20, true); assert(mnc_pending_n4() == 2); /* Reused pool ID. */
    mnc_ack(2, 22, true); mnc_ack(2, 22, true); assert(mnc_pending_n4() == 1);
    mnc_seal(); assert(replies == 0);
    assert(mnc_track(3, 23) < 0);
    mnc_ack(1, 21, true); assert(replies == 1 && successes == 1);
    mnc_ack(1, 21, true); assert(replies == 1);
    assert(mnc_begin(&gate, 42, 7) == 0);
    mnc_seal(); assert(replies == 2 && successes == 1); /* No N4 is not proof. */
    assert(mnc_begin(&gate, 42, 7) == 0);
    assert(mnc_track(1, 23) == 0); mnc_ack(1, 23, true);
    assert(replies == 2); mnc_seal(); assert(replies == 3 && successes == 2);
    assert(mnc_begin(&gate, 42, 7) == 0);
    assert(mnc_track(1, 24) == 0 && mnc_track(2, 25) == 0);
    mnc_seal(); mnc_ack(1, 24, false); assert(replies == 3);
    mnc_ack(2, 25, true); assert(replies == 4 && successes == 2);
    assert(mnc_begin(&gate, 42, 7) == 0);
    for (i = 1; i <= MNC_MAX_TRANSACTIONS; i++) assert(mnc_track(i, i) == 0);
    assert(mnc_track(MNC_MAX_TRANSACTIONS + 1, 0) < 0);
    mnc_cancel(); assert(replies == 5 && successes == 2);
    assert(mnc_begin(&gate, 42, 7) == 0);
    assert(mnc_track(1, 26) == 0); mnc_seal();
    assert(mf_fence(&gate, 2, boot, 1000) == 0);
    mnc_ack(1, 26, true); assert(replies == 6 && successes == 2);
    assert(mf_prepare(&gate, 2, 0, 42, cookie) == 0);
    assert(mnc_begin(&gate, 42, 7) == 0);
    assert(mnc_track(1, 27) == 0); mnc_seal();
    now_ns += 1001; mnc_poll(); assert(replies == 7 && successes == 2);
    mnc_ack(1, 27, true); assert(replies == 7);
    mf_close(&gate);
    snprintf(file, sizeof(file), "%s/fence.state", dir); assert(unlink(file) == 0);
    snprintf(file, sizeof(file), "%s/fence.lock", dir); assert(unlink(file) == 0);
    assert(rmdir(dir) == 0);
    puts("PASS native completion: no early N7 ACK, all N4 replies, reused IDs, negative/duplicate/late ACK, fence, timeout, bounded capacity");
    return 0;
}
