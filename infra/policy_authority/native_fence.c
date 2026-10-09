/* SPDX-License-Identifier: AGPL-3.0-or-later */
#define _GNU_SOURCE
#include "native_fence.h"
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/file.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static bool mf_uuid(const char *s)
{
    size_t i;
    if (!s || strlen(s) != 36) return false;
    for (i = 0; i < 36; i++) {
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (s[i] != '-') return false;
        } else if (!((s[i] >= '0' && s[i] <= '9') || (s[i] >= 'a' && s[i] <= 'f')))
            return false;
    }
    return true;
}

static void mf_clear(mf_gate *g)
{
    memset(g->cookie, 0, sizeof(g->cookie));
    g->prepared = false;
    g->system = false;
    g->admission_closed = true;
    g->target_seid = 0;
    if (g->serial == UINT64_MAX) g->failed = true;
    else g->serial++;
}

static int mf_store(mf_gate *g, uint64_t token, uint64_t version, const char *boot)
{
    char temp[80], line[160];
    int dir = -1, fd = -1, length, result = -1;
    struct stat st;
    ssize_t written;
    snprintf(temp, sizeof(temp), ".fence-%ld.tmp", (long)getpid());
    length = snprintf(line, sizeof(line), "MF1 %" PRIu64 " %" PRIu64 " %s\n", token, version, boot);
    if (length < 0 || (size_t)length >= sizeof(line)) goto done;
    dir = open(g->state_dir, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (dir < 0 || fstat(dir, &st) || st.st_uid != geteuid() || (st.st_mode & 0077)) goto done;
    /* A leftover temporary file must be explicitly reconciled, never followed. */
    fd = openat(dir, temp, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0) goto done;
    do { written = write(fd, line, (size_t)length); } while (written < 0 && errno == EINTR);
    if (written != length || fsync(fd)) goto done;
    if (close(fd)) { fd = -1; goto done; }
    fd = -1;
    if (renameat(dir, temp, dir, "fence.state") || fsync(dir)) goto done;
    result = 0;
done:
    if (fd >= 0) close(fd);
    if (dir >= 0) {
        /* Only this process's fixed temporary filename inside the checked dir. */
        if (result) unlinkat(dir, temp, 0);
        close(dir);
    }
    if (result) { g->failed = true; mf_clear(g); }
    return result;
}

int mf_open(mf_gate *g, const char *state_dir, mf_clock_fn clock)
{
    int dir = -1, fd = -1, consumed = 0;
    bool created = false;
    ssize_t count;
    char line[161], token_text[32], version_text[32], boot[37], *end;
    unsigned long long token, version;
    struct stat st;
    memset(g, 0, sizeof(*g));
    g->lock_fd = -1;
    g->failed = true;
    if (!clock || !state_dir || strlen(state_dir) >= sizeof(g->state_dir)) return -1;
    g->clock = clock;
    strcpy(g->state_dir, state_dir);
    dir = open(state_dir, O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (dir < 0 || fstat(dir, &st) || st.st_uid != geteuid() || (st.st_mode & 0077)) goto invalid;
    g->lock_fd = openat(dir, "fence.lock", O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (g->lock_fd >= 0) created = true;
    else if (errno == EEXIST)
        g->lock_fd = openat(dir, "fence.lock", O_RDWR | O_CLOEXEC | O_NOFOLLOW);
    if (g->lock_fd < 0 || fstat(g->lock_fd, &st) || !S_ISREG(st.st_mode) || st.st_nlink != 1 ||
            st.st_uid != geteuid() || (st.st_mode & 0077) || flock(g->lock_fd, LOCK_EX | LOCK_NB))
        goto invalid;
    fd = openat(dir, "fence.state", O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) {
        if (errno != ENOENT || !created) goto invalid;
        close(dir);
        dir = -1;
        if (mf_store(g, 0, 0, "00000000-0000-0000-0000-000000000000")) goto invalid;
        g->failed = false;
        return 0;
    }
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_nlink != 1 ||
            st.st_uid != geteuid() || (st.st_mode & 0077)) goto invalid;
    do { count = read(fd, line, sizeof(line) - 1); } while (count < 0 && errno == EINTR);
    if (count <= 0 || count >= (ssize_t)(sizeof(line) - 1)) goto invalid;
    line[count] = 0;
    if (sscanf(line, "MF1 %31[0-9] %31[0-9] %36s\n%n", token_text, version_text, boot, &consumed) != 3 ||
            consumed != count || !mf_uuid(boot)) goto invalid;
    errno = 0; token = strtoull(token_text, &end, 10);
    if (errno || *end) goto invalid;
    errno = 0; version = strtoull(version_text, &end, 10);
    if (errno || *end) goto invalid;
    g->token = token; g->version = version; strcpy(g->core_boot, boot);
    /* A process restart never resurrects an old lease or action cookie. */
    g->deadline_ns = 0; g->failed = false;
    g->recovery_required = g->token != 0;
    close(fd); close(dir);
    return 0;
invalid:
    if (fd >= 0) close(fd);
    if (dir >= 0) close(dir);
    if (g->lock_fd >= 0) { close(g->lock_fd); g->lock_fd = -1; }
    return -1;
}

void mf_close(mf_gate *g)
{
    mf_clear(g);
    g->failed = true;
    if (g->lock_fd >= 0) { close(g->lock_fd); g->lock_fd = -1; }
}

bool mf_leased(const mf_gate *g)
{
    return !g->failed && g->clock && g->deadline_ns > g->clock();
}

int mf_fence(mf_gate *g, uint64_t token, const char *core_boot, uint64_t remaining_ns)
{
    uint64_t now;
    if (g->failed || !mf_uuid(core_boot) || remaining_ns > MF_MAX_LEASE_NS || token < g->token || !token)
        return -1;
    if (token == g->token) {
        /* Replaying a fence must not extend its deadline or resurrect a cookie. */
        return strcmp(core_boot, g->core_boot) ? -1 : 0;
    }
    now = g->clock();
    if (now > UINT64_MAX - remaining_ns) return -1;
    mf_clear(g);
    if (g->failed || mf_store(g, token, g->version, core_boot)) return -1;
    g->token = token; strcpy(g->core_boot, core_boot);
    g->deadline_ns = remaining_ns ? now + remaining_ns : 0;
    g->recovery_required = false;
    return 0;
}

int mf_prepare(mf_gate *g, uint64_t token, uint64_t version, uint64_t seid,
               const unsigned char cookie[MF_COOKIE_BYTES])
{
    unsigned char nonzero = 0;
    size_t i;
    if (!cookie || !mf_leased(g) || token != g->token || version != g->version || g->prepared)
        return -1;
    for (i = 0; i < MF_COOKIE_BYTES; i++) nonzero |= cookie[i];
    if (!nonzero) return -1;
    mf_clear(g);
    if (g->failed) return -1;
    memcpy(g->cookie, cookie, sizeof(g->cookie));
    g->target_seid = seid; g->prepared = true;
    return 0;
}

bool mf_authorized(const mf_gate *g, uint64_t token, uint64_t version,
                   uint64_t seid, const unsigned char cookie[MF_COOKIE_BYTES])
{
    unsigned char mismatch = 0;
    size_t i;
    if (!cookie || !mf_leased(g) || !g->prepared || token != g->token ||
            version != g->version || (!g->system && seid != g->target_seid)) return false;
    for (i = 0; i < MF_COOKIE_BYTES; i++) mismatch |= cookie[i] ^ g->cookie[i];
    return mismatch == 0;
}

int mf_prepare_system(mf_gate *g, uint64_t token, uint64_t version,
                      const unsigned char cookie[MF_COOKIE_BYTES])
{
    if (mf_prepare(g, token, version, 0, cookie)) return -1;
    g->system = true;
    g->admission_closed = true;
    return 0;
}

bool mf_system_ready(const mf_gate *g)
{
    return mf_leased(g) && g->system && g->prepared;
}

bool mf_system_admit(const mf_gate *g)
{
    return mf_system_ready(g) && !g->admission_closed;
}

int mf_system_admission(mf_gate *g, uint64_t token, bool open)
{
    if (token != g->token || !mf_system_ready(g)) return -1;
    g->admission_closed = !open;
    return 0;
}

int mf_finish(mf_gate *g, uint64_t token, uint64_t version)
{
    if (!mf_leased(g) || !g->prepared || token != g->token || version != g->version || version == UINT64_MAX)
        return -1;
    mf_clear(g);
    if (g->failed || mf_store(g, token, version + 1, g->core_boot)) return -1;
    g->version = version + 1;
    return 0;
}

void mf_cancel(mf_gate *g)
{
    mf_clear(g);
}

static void mf_put64(unsigned char *p, uint64_t n)
{
    int i;
    for (i = 7; i >= 0; i--) { p[i] = (unsigned char)n; n >>= 8; }
}

static uint64_t mf_get64(const unsigned char *p)
{
    int i;
    uint64_t n = 0;
    for (i = 0; i < 8; i++) n = (n << 8) | p[i];
    return n;
}

int mf_encode(const mf_gate *g, uint64_t seid, unsigned char out[MF_ENVELOPE_BYTES])
{
    if (!out || !mf_authorized(g, g->token, g->version, seid, g->cookie)) return -1;
    memcpy(out, "M5GFENCE", 8); out[8] = g->system ? 2 : 1;
    mf_put64(out + 9, g->token); mf_put64(out + 17, g->version);
    memcpy(out + 25, g->cookie, MF_COOKIE_BYTES);
    return 0;
}

int mf_decode(const mf_gate *g, uint64_t seid, const unsigned char *data, size_t size,
              uint64_t *token, uint64_t *serial)
{
    uint64_t received_token, version;
    if (!data || !token || !serial || size != MF_ENVELOPE_BYTES || memcmp(data, "M5GFENCE", 8) ||
            data[8] != (g->system ? 2 : 1))
        return -1;
    received_token = mf_get64(data + 9); version = mf_get64(data + 17);
    if (!mf_authorized(g, received_token, version, seid, data + 25)) return -1;
    *token = received_token; *serial = g->serial;
    return 0;
}

bool mf_stamp_valid(const mf_gate *g, uint64_t token, uint64_t serial, uint64_t seid)
{
    return mf_leased(g) && g->prepared && token == g->token && serial == g->serial &&
        (g->system || seid == g->target_seid);
}
