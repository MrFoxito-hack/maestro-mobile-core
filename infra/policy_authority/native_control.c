/* SPDX-License-Identifier: AGPL-3.0-or-later */
#define _GNU_SOURCE
#include "native_control.h"
#include "native_completion.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <errno.h>
#include <inttypes.h>

mf_gate maestro_native_gate;
static bool enabled;
static uint64_t entered_token, entered_serial, entered_target;

int mfc_open(const char *state_dir, mf_clock_fn clock)
{
    if (enabled) return -1;
    if (!state_dir) return 0;
    /* Once opted in, an initialization error must never disable enforcement. */
    enabled = true;
    return mf_open(&maestro_native_gate, state_dir, clock);
}

void mfc_close(void)
{
    mnc_cancel();
    mfc_leave();
    if (enabled) mf_close(&maestro_native_gate);
    /* Keep enabled=true: late callbacks must still fail closed. */
}

bool mfc_enabled(void) { return enabled; }
bool mfc_system_admit(void)
{
    if (!enabled) return true;
    if (!maestro_native_gate.failed && !maestro_native_gate.token)
        return !maestro_native_gate.admission_closed;
    return mf_system_admit(&maestro_native_gate);
}

static int unhex(const char *hex, unsigned char *out, size_t count)
{
    size_t i;
    if (!hex || strlen(hex) != count * 2) return -1;
    for (i = 0; i < count; i++) {
        char a = hex[2 * i], b = hex[2 * i + 1];
        int hi = a >= '0' && a <= '9' ? a - '0' : a >= 'a' && a <= 'f' ? a - 'a' + 10 : -1;
        int lo = b >= '0' && b <= '9' ? b - '0' : b >= 'a' && b <= 'f' ? b - 'a' + 10 : -1;
        if (hi < 0 || lo < 0) return -1;
        out[i] = (unsigned char)((hi << 4) | lo);
    }
    return 0;
}

static void hexify(const unsigned char *bytes, size_t length, char *out)
{
    static const char digits[] = "0123456789abcdef";
    size_t i;
    for (i = 0; i < length; i++) {
        out[2 * i] = digits[bytes[i] >> 4]; out[2 * i + 1] = digits[bytes[i] & 15];
    }
    out[2 * length] = 0;
}

int mfc_enter(const char *hex, uint64_t target)
{
    unsigned char frame[MF_ENVELOPE_BYTES];
    mfc_leave();
    if (!enabled) return 0;
    /* Only native lifecycle writers may use the separate system grant. */
    if (maestro_native_gate.system) return -1;
    if (unhex(hex, frame, sizeof(frame)) ||
            mf_decode(&maestro_native_gate, target, frame, sizeof(frame), &entered_token, &entered_serial))
        return -1;
    entered_target = target;
    return 0;
}

void mfc_leave(void)
{
    entered_token = entered_serial = entered_target = 0;
}

bool mfc_current(uint64_t target)
{
    if (!enabled) return true;
    return !maestro_native_gate.system && target == entered_target &&
        mf_stamp_valid(&maestro_native_gate, entered_token, entered_serial, target);
}

int mfc_export(uint64_t target, char out[MFC_HEX_BYTES])
{
    unsigned char frame[MF_ENVELOPE_BYTES];
    if (!enabled) { out[0] = 0; return 0; }
    if (!mfc_current(target) || mf_encode(&maestro_native_gate, target, frame)) return -1;
    hexify(frame, sizeof(frame), out);
    return 0;
}

static int number(const char *s, uint64_t *out)
{
    const char *p;
    char *end;
    unsigned long long value;
    if (!s || !*s) return -1;
    for (p = s; *p; p++) if (*p < '0' || *p > '9') return -1;
    errno = 0; value = strtoull(s, &end, 10);
    if (errno || *end) return -1;
    *out = value;
    return 0;
}

int mfc_control(const char *request, size_t length, char *reply, size_t capacity)
{
    char buffer[512], *fields[7], *save, *word;
    unsigned char cookie[MF_COOKIE_BYTES];
    size_t count = 0;
    uint64_t token, version, value;
    int rc = -1;
    const char *error = "invalid_native_control";
    if (!enabled) { error = "native_control_not_enabled"; goto response; }
    if (!length || length >= sizeof(buffer) || memchr(request, 0, length) || request[length - 1] != '\n')
        goto response;
    memcpy(buffer, request, length); buffer[length] = 0;
    for (word = strtok_r(buffer, " \r\n", &save); word; word = strtok_r(NULL, " \r\n", &save)) {
        if (count == 7) goto response;
        fields[count++] = word;
    }
    if (!count) goto response;
    error = "native_fence_rejected";
    if (!strcmp(fields[0], "bootstrap-close-v1") && count == 1 &&
            !maestro_native_gate.failed && !maestro_native_gate.token) {
        maestro_native_gate.admission_closed = true; rc = 0;
    }
    else if (!strcmp(fields[0], "fence-v1") && count == 4 && !number(fields[1], &token) && !number(fields[2], &value))
        rc = mf_fence(&maestro_native_gate, token, fields[3], value);
    else if (!strcmp(fields[0], "fence-at-v1") && count == 4 && !number(fields[1], &token) &&
             !number(fields[2], &value) && !maestro_native_gate.failed && maestro_native_gate.clock) {
        uint64_t now = maestro_native_gate.clock();
        /* Absolute NF-monotonic deadline: network transit cannot extend it. */
        if (value > now) rc = mf_fence(&maestro_native_gate, token, fields[3], value - now);
    }
    else if (!strcmp(fields[0], "prepare-v1") && count == 5 && !number(fields[1], &token) &&
             !number(fields[2], &version) && !number(fields[3], &value) && !unhex(fields[4], cookie, sizeof(cookie)))
        rc = mf_prepare(&maestro_native_gate, token, version, value, cookie);
    else if (!strcmp(fields[0], "system-prepare-v1") && count == 4 && !number(fields[1], &token) &&
             !number(fields[2], &version) && !unhex(fields[3], cookie, sizeof(cookie)))
        rc = mf_prepare_system(&maestro_native_gate, token, version, cookie);
    else if ((!strcmp(fields[0], "system-open-v1") || !strcmp(fields[0], "system-close-v1")) &&
             count == 2 && !number(fields[1], &token))
        rc = mf_system_admission(&maestro_native_gate, token, !strcmp(fields[0], "system-open-v1"));
    else if (!strcmp(fields[0], "finish-v1") && count == 3 && !mnc_pending() && !number(fields[1], &token) && !number(fields[2], &version))
        rc = mf_finish(&maestro_native_gate, token, version);
    else if (!strcmp(fields[0], "cancel-v1") && count == 2 && !number(fields[1], &token) &&
             token == maestro_native_gate.token) {
        mf_cancel(&maestro_native_gate); mnc_cancel(); rc = 0;
    }
    mnc_poll();
response:
    if (rc == 0)
        return snprintf(reply, capacity, "{\"status\":\"success\",\"data\":{\"fencing_token\":\"%" PRIu64
            "\",\"version\":\"%" PRIu64 "\",\"prepared\":%s}}", maestro_native_gate.token,
            maestro_native_gate.version, maestro_native_gate.prepared ? "true" : "false");
    return snprintf(reply, capacity, "{\"status\":\"blocked\",\"error_code\":\"%s\"}", error);
}
