/* SPDX-License-Identifier: AGPL-3.0-or-later */
#ifndef MAESTRO_NATIVE_PFCP_H
#define MAESTRO_NATIVE_PFCP_H
#include "native_fence.h"
/* Private IE under an explicitly configured 16-bit Enterprise ID. No IANA
 * allocation is claimed. Tests use RFC 5612's documentation PEN only.
 * Layout follows TS 29.244 section 8.1.1: type, length, enterprise, value.
 */
#define MFP_IE_TYPE 0xff01
#define MFP_IE_BYTES (6 + MF_ENVELOPE_BYTES)
int mfp_append(unsigned char *payload, size_t *length, size_t capacity,
               uint16_t enterprise, const unsigned char frame[MF_ENVELOPE_BYTES]);
/* Validate the entire TLV stream, then remove exactly one authority IE.
 * Returns 1 if extracted, 0 if absent, -1 on malformed/duplicate input.
 * On error, the packet and length remain unchanged. Full PFCP datagram input.
 */
int mfp_extract(unsigned char *packet, size_t *length, uint16_t enterprise,
                unsigned char frame[MF_ENVELOPE_BYTES], uint64_t *seid);
/* Conservative accounting exception: nonempty, well-framed Update URR/Query
 * URR only. No Create/Remove URR, PDR, FAR, QER, unknown or vendor IEs allowed.
 * Group contents still pass through the normal Open5GS parser/CHF handling.
 */
bool mfp_urr_only(const unsigned char *payload, size_t length);
#endif
