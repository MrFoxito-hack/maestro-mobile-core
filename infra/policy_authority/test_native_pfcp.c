/* SPDX-License-Identifier: AGPL-3.0-or-later */
#include "native_pfcp.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static void put_length(unsigned char *p, size_t n) { p[2] = (n - 4) >> 8; p[3] = n - 4; }

int main(void)
{
    unsigned char packet[1024] = {0x21, 52}, saved[1024], original[1024];
    unsigned char frame[MF_ENVELOPE_BYTES], actual[MF_ENVELOPE_BYTES];
    unsigned char urr[] = {0, 13, 0, 8, 0, 81, 0, 4, 0, 0, 0, 1};
    size_t length, payload, before, i;
    uint64_t seid;
    const uint16_t example_pen = 32473; /* RFC 5612, test fixture only. */
    packet[11] = 42;
    memcpy(packet + 16, urr, sizeof(urr)); length = 16 + sizeof(urr);
    put_length(packet, length); memcpy(original, packet, length);
    memset(frame, 0xA5, sizeof(frame));
    payload = sizeof(urr);
    assert(mfp_append(packet + 16, &payload, sizeof(packet) - 16, example_pen, frame) == 0);
    length = payload + 16; put_length(packet, length); before = length;
    memcpy(saved, packet, length);
    assert(mfp_extract(packet, &length, example_pen, actual, &seid) == 1);
    assert(seid == 42 && length == 16 + sizeof(urr));
    assert(!memcmp(actual, frame, sizeof(frame)) && !memcmp(packet, original, length));
    assert(mfp_extract(packet, &length, example_pen, actual, &seid) == 0);
    /* Every possible truncation, including self-consistent outer lengths. */
    for (i = 0; i < before; i++) {
        memcpy(packet, saved, before); length = i;
        assert(mfp_extract(packet, &length, example_pen, actual, &seid) < 0);
        assert(length == i && !memcmp(packet, saved, before));
        if (i > 16 + sizeof(urr)) {
            put_length(packet, i); memcpy(original, packet, before);
            assert(mfp_extract(packet, &length, example_pen, actual, &seid) < 0);
            assert(!memcmp(packet, original, before));
        }
    }
    memcpy(packet, saved, before); length = before;
    assert(mfp_extract(packet, &length, example_pen + 1, actual, &seid) == 0 && length == before);
    /* Duplicate authority IEs cannot hide a stale token behind a valid one. */
    memcpy(packet + before, saved + 16 + sizeof(urr), MFP_IE_BYTES);
    length += MFP_IE_BYTES; put_length(packet, length);
    assert(mfp_extract(packet, &length, example_pen, actual, &seid) < 0);
    payload = sizeof(urr);
    assert(mfp_append(urr, &payload, sizeof(urr), example_pen, frame) < 0 && payload == sizeof(urr));
    assert(mfp_urr_only(urr, sizeof(urr)));
    urr[1] = 77; assert(mfp_urr_only(urr, sizeof(urr)));
    for (i = 0; i < 256; i++) {
        urr[1] = i;
        assert(mfp_urr_only(urr, sizeof(urr)) == (i == 13 || i == 77));
    }
    urr[1] = 13;
    for (i = 0; i < sizeof(urr); i++) assert(!mfp_urr_only(urr, i));
    memcpy(packet, urr, sizeof(urr)); memcpy(packet + sizeof(urr), urr, sizeof(urr));
    packet[sizeof(urr) + 1] = 14; /* Update QER mixed into charging. */
    assert(!mfp_urr_only(packet, sizeof(urr) * 2));
    puts("PASS native PFCP: roundtrip, exact SEID, bounded TLVs, truncation, duplicates, QER never exempted as URR");
    return 0;
}
