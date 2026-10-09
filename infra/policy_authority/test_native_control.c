/* SPDX-License-Identifier: AGPL-3.0-or-later */
#define _GNU_SOURCE
#include "native_control.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static uint64_t now_ns = 100;
static uint64_t clock_ns(void) { return now_ns; }
static const char boot[] = "01234567-89ab-cdef-0123-456789abcdef";
static char reply[512];

static void control(const char *request, bool success)
{
    int length = mfc_control(request, strlen(request), reply, sizeof(reply));
    assert(length > 0 && (size_t)length < sizeof(reply));
    assert(strstr(reply, success ? "\"success\"" : "\"blocked\""));
}

int main(void)
{
    char dir[] = "/tmp/maestro-control-test-XXXXXX", request[512], file[256];
    char envelope[MFC_HEX_BYTES], corrupt[MFC_HEX_BYTES];
    const char cookie[] = "a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5";
    size_t i;
    control("fence-v1 1 1000 ignored\n", false);
    assert(mfc_open(NULL, clock_ns) == 0 && !mfc_enabled());
    assert(mfc_current(42) && mfc_enter(NULL, 42) == 0);
    assert(mfc_export(42, envelope) == 0 && !envelope[0]);
    assert(mkdtemp(dir));
    assert(mfc_open(dir, clock_ns) == 0 && mfc_enabled());
    assert(!mfc_current(42) && mfc_enter(NULL, 42) < 0);
    control("\n", false);
    control("fence-v1 -1 1000 invalid\n", false);
    control("fence-v1 18446744073709551616 1000 invalid\n", false);
    snprintf(request, sizeof(request), "fence-v1 9007199254740993 1000 %s\n", boot);
    control(request, true);
    assert(strstr(reply, "9007199254740993"));
    snprintf(request, sizeof(request), "prepare-v1 9007199254740993 0 42 %s\n", cookie);
    control(request, true);
    assert(!mfc_current(42)); /* A permit alone must never authorize a writer. */
    {
        unsigned char frame[MF_ENVELOPE_BYTES];
        assert(mf_encode(&maestro_native_gate, 42, frame) == 0);
        for (i = 0; i < sizeof(frame); i++) snprintf(envelope + i * 2, 3, "%02x", frame[i]);
    }
    assert(mfc_enter(envelope, 43) < 0);
    for (i = 0; i < strlen(envelope); i++) {
        strcpy(corrupt, envelope); corrupt[i] = corrupt[i] == '0' ? '1' : '0';
        assert(mfc_enter(corrupt, 42) < 0 && !mfc_current(42));
    }
    assert(mfc_enter(envelope, 42) == 0 && mfc_current(42));
    assert(!mfc_current(43));
    assert(mfc_export(42, corrupt) == 0 && !strcmp(corrupt, envelope));
    mfc_leave();
    assert(!mfc_current(42) && mfc_export(42, corrupt) < 0);
    assert(mfc_enter(envelope, 42) == 0);
    control("cancel-v1 9007199254740992\n", false);
    assert(mfc_current(42));
    control("cancel-v1 9007199254740993\n", true);
    assert(!mfc_current(42) && mfc_enter(envelope, 42) < 0);
    control(request, true);
    assert(mfc_enter(envelope, 42) == 0);
    now_ns += 1001;
    assert(!mfc_current(42));
    control("finish-v1 9007199254740993 0\n", false);
    snprintf(request, sizeof(request), "fence-at-v1 9007199254740994 %llu %s\n",
        (unsigned long long)(now_ns + 1000), boot);
    control(request, true);
    assert(maestro_native_gate.deadline_ns == now_ns + 1000);
    snprintf(request, sizeof(request), "system-prepare-v1 9007199254740994 0 %s\n", cookie);
    control(request, true);
    assert(mf_system_ready(&maestro_native_gate) && !mfc_system_admit());
    control("system-open-v1 9007199254740993\n", false);
    control("system-open-v1 9007199254740994\n", true);
    assert(mfc_system_admit());
    {
        unsigned char frame[MF_ENVELOPE_BYTES];
        uint64_t token, serial;
        assert(mf_encode(&maestro_native_gate, 99, frame) == 0 && frame[8] == 2);
        assert(mf_decode(&maestro_native_gate, 123, frame, sizeof(frame), &token, &serial) == 0);
        for (i = 0; i < sizeof(frame); i++) snprintf(envelope + i * 2, 3, "%02x", frame[i]);
        assert(mfc_enter(envelope, 99) < 0 && !mfc_current(99));
        control("system-close-v1 9007199254740994\n", true);
        assert(!mfc_system_admit() && mf_system_ready(&maestro_native_gate));
        assert(mf_stamp_valid(&maestro_native_gate, token, serial, 123));
        frame[8] = 1; /* A system grant cannot impersonate an owner's mutation. */
        assert(mf_decode(&maestro_native_gate, 123, frame, sizeof(frame), &token, &serial) < 0);
        now_ns += 1001;
        assert(!mf_system_ready(&maestro_native_gate) && !mfc_system_admit());
        control("system-open-v1 9007199254740994\n", false);
    }
    mfc_close();
    assert(mfc_enabled() && !mfc_current(42));
    snprintf(file, sizeof(file), "%s/fence.state", dir); assert(unlink(file) == 0);
    snprintf(file, sizeof(file), "%s/fence.lock", dir); assert(unlink(file) == 0);
    assert(rmdir(dir) == 0);
    puts("PASS native control: opt-in, exact 64-bit token, reject malformed requests, scoped N7 envelope, expiry, cancellation");
    return 0;
}
