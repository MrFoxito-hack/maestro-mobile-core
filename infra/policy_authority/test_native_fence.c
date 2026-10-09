/* SPDX-License-Identifier: AGPL-3.0-or-later */
#define _GNU_SOURCE
#include "native_fence.h"
#include <sys/stat.h>
#include <unistd.h>
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint64_t now_ns = 100;
static uint64_t clock_ns(void) { return now_ns; }
static const char *boot = "01234567-89ab-cdef-0123-456789abcdef";

int main(void)
{
    char dir[] = "/tmp/maestro-fence-test-XXXXXX", file[256], wrong[256];
    mf_gate g, restarted, concurrent;
    unsigned char cookie[MF_COOKIE_BYTES], frame[MF_ENVELOPE_BYTES], invalid[MF_ENVELOPE_BYTES];
    uint64_t token, serial, deadline;
    FILE *f;
    size_t i;
    assert(mkdtemp(dir));
    memset(cookie, 0xA5, sizeof(cookie));
    assert(mf_open(&g, dir, clock_ns) == 0);
    assert(mf_open(&concurrent, dir, clock_ns) < 0);
    assert(!mf_leased(&g));
    assert(mf_prepare(&g, 0, 0, 42, cookie) < 0);
    assert(mf_fence(&g, 1, boot, 1000) == 0);
    deadline = g.deadline_ns;
    now_ns++;
    assert(mf_fence(&g, 1, boot, 5000) == 0 && g.deadline_ns == deadline);
    assert(mf_fence(&g, 1, "11234567-89ab-cdef-0123-456789abcdef", 5000) < 0);
    assert(mf_fence(&g, 0, boot, 1000) < 0);
    assert(mf_fence(&g, 2, boot, MF_MAX_LEASE_NS + 1) < 0);
    assert(mf_prepare(&g, 1, 1, 42, cookie) < 0);
    assert(mf_prepare(&g, 1, 0, 42, cookie) == 0);
    assert(mf_prepare(&g, 1, 0, 42, cookie) < 0);
    assert(mf_encode(&g, 41, frame) < 0);
    assert(mf_encode(&g, 42, frame) == 0);
    assert(mf_decode(&g, 42, frame, sizeof(frame), &token, &serial) == 0);
    assert(mf_stamp_valid(&g, token, serial, 42));
    for (i = 0; i < sizeof(frame); i++) {
        memcpy(invalid, frame, sizeof(frame)); invalid[i] ^= 1;
        assert(mf_decode(&g, 42, invalid, sizeof(invalid), &token, &serial) < 0);
    }
    for (i = 0; i < sizeof(frame); i++)
        assert(mf_decode(&g, 42, frame, i, &token, &serial) < 0);
    assert(mf_finish(&g, 1, 0) == 0 && g.version == 1);
    assert(!mf_stamp_valid(&g, token, serial, 42));
    assert(mf_decode(&g, 42, frame, sizeof(frame), &token, &serial) < 0);
    mf_close(&g);
    assert(mf_open(&restarted, dir, clock_ns) == 0);
    assert(restarted.token == 1 && restarted.version == 1 && !mf_leased(&restarted));
    assert(restarted.recovery_required);
    assert(mf_fence(&restarted, 1, boot, 1000) == 0 && !mf_leased(&restarted));
    assert(restarted.recovery_required);
    assert(mf_fence(&restarted, 2, boot, 1000) == 0);
    assert(!restarted.recovery_required);
    assert(mf_prepare(&restarted, 2, 1, 42, cookie) == 0);
    assert(mf_encode(&restarted, 42, frame) == 0);
    assert(mf_decode(&restarted, 42, frame, sizeof(frame), &token, &serial) == 0);
    assert(mf_fence(&restarted, 3, boot, 0) == 0);
    assert(!mf_stamp_valid(&restarted, token, serial, 42));
    assert(mf_fence(&restarted, 4, boot, 1000) == 0);
    assert(mf_prepare(&restarted, 4, 1, 42, cookie) == 0);
    assert(mf_encode(&restarted, 42, frame) == 0);
    now_ns += 1001;
    assert(mf_decode(&restarted, 42, frame, sizeof(frame), &token, &serial) < 0);
    assert(mf_finish(&restarted, 4, 1) < 0);
    now_ns = UINT64_MAX - 100;
    assert(mf_fence(&restarted, 5, boot, 1000) < 0);
    now_ns = 5000;
    /* A write failure must stop admission even with an unexpired old lease. */
    assert(mf_fence(&restarted, 5, boot, 1000) == 0);
    assert(mf_prepare(&restarted, 5, 1, 42, cookie) == 0);
    snprintf(wrong, sizeof(wrong), "%s/missing", dir);
    strcpy(restarted.state_dir, wrong);
    assert(mf_finish(&restarted, 5, 1) < 0 && restarted.failed);
    assert(!mf_authorized(&restarted, 5, 1, 42, cookie));
    mf_close(&restarted);
    /* Corrupt persistent state may never reset the floor to zero. */
    snprintf(file, sizeof(file), "%s/fence.state", dir);
    f = fopen(file, "w"); assert(f); fputs("MF1 -1 0 invalid\n", f); fclose(f);
    assert(mf_open(&restarted, dir, clock_ns) < 0 && restarted.failed);
    assert(unlink(file) == 0);
    assert(mf_open(&restarted, dir, clock_ns) < 0 && restarted.failed);
    assert(symlink("/etc/passwd", file) == 0);
    assert(mf_open(&restarted, dir, clock_ns) < 0 && restarted.failed);
    assert(unlink(file) == 0);
    assert(chmod(dir, 0755) == 0);
    assert(mf_open(&restarted, dir, clock_ns) < 0);
    snprintf(file, sizeof(file), "%s/fence.lock", dir);
    assert(unlink(file) == 0);
    assert(rmdir(dir) == 0);
    puts("PASS native fence: persistence, expiry, epochs, queued stamps, cookie integrity, 64-bit framing, fail closed");
    return 0;
}
