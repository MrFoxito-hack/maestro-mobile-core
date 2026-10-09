/* SPDX-License-Identifier: AGPL-3.0-or-later */
#include "native_pfcp.h"
#include <string.h>

static unsigned u16(const unsigned char *p) { return ((unsigned)p[0] << 8) | p[1]; }
static void put16(unsigned char *p, unsigned n) { p[0] = n >> 8; p[1] = n; }

static int scan(const unsigned char *data, size_t length, uint16_t enterprise, size_t *found)
{
    size_t offset = 0, value;
    int count = 0;
    while (offset < length) {
        if (length - offset < 4) return -1;
        value = u16(data + offset + 2);
        if (value > length - offset - 4) return -1;
        if (u16(data + offset) == MFP_IE_TYPE) {
            if (value < 2) return -1;
            if (u16(data + offset + 4) == enterprise) {
                if (++count > 1 || value != MFP_IE_BYTES - 4) return -1;
                *found = offset;
            }
        }
        offset += 4 + value;
    }
    return count;
}

int mfp_append(unsigned char *payload, size_t *length, size_t capacity,
               uint16_t enterprise, const unsigned char frame[MF_ENVELOPE_BYTES])
{
    size_t found;
    if (!payload || !length || !frame || !enterprise || *length > capacity ||
            capacity - *length < MFP_IE_BYTES || *length > 65535 - 12 - MFP_IE_BYTES ||
            scan(payload, *length, enterprise, &found) != 0) return -1;
    put16(payload + *length, MFP_IE_TYPE);
    put16(payload + *length + 2, MFP_IE_BYTES - 4);
    put16(payload + *length + 4, enterprise);
    memcpy(payload + *length + 6, frame, MF_ENVELOPE_BYTES);
    *length += MFP_IE_BYTES;
    return 0;
}

int mfp_extract(unsigned char *packet, size_t *length, uint16_t enterprise,
                unsigned char frame[MF_ENVELOPE_BYTES], uint64_t *seid)
{
    size_t header, found = 0, i, offset;
    uint64_t target = 0;
    int count;
    if (!packet || !length || !frame || !seid || !enterprise || *length < 8 ||
            packet[0] >> 5 != 1 || (size_t)u16(packet + 2) + 4 != *length) return -1;
    header = (packet[0] & 1) ? 16 : 8;
    if (*length < header) return -1;
    if (header == 16) for (i = 4; i < 12; i++) target = (target << 8) | packet[i];
    count = scan(packet + header, *length - header, enterprise, &found);
    if (count < 0) return -1;
    *seid = target;
    if (!count) return 0;
    offset = header + found;
    memcpy(frame, packet + offset + 6, MF_ENVELOPE_BYTES);
    memmove(packet + offset, packet + offset + MFP_IE_BYTES, *length - offset - MFP_IE_BYTES);
    *length -= MFP_IE_BYTES;
    put16(packet + 2, (unsigned)(*length - 4));
    return 1;
}

bool mfp_urr_only(const unsigned char *payload, size_t length)
{
    size_t offset = 0, size;
    unsigned type;
    if (!payload || !length) return false;
    while (offset < length) {
        if (length - offset < 4) return false;
        type = u16(payload + offset); size = u16(payload + offset + 2);
        /* TS 29.244: Update URR=13, Query URR=77. */
        if ((type != 13 && type != 77) || size < 8 || size > length - offset - 4) return false;
        offset += size + 4;
    }
    return true;
}
