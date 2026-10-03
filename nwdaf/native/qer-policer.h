/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Allocation-free per-QER, per-direction token bucket. Caller supplies monotonic
 * microseconds and serialized access (Open5GS packet/event thread).
 * This is policing (drop excess), not shaping or a GBR scheduler.
 */
#ifndef MAESTRO_QER_POLICER_H
#define MAESTRO_QER_POLICER_H
#include <stdbool.h>
#include <stdint.h>

typedef struct maestro_qer_bucket_s {
    uint64_t rate_bps;
    uint64_t tokens_bits;
    uint64_t fraction;
    uint64_t last_us;
    uint64_t dropped_packets;
    bool initialized;
} maestro_qer_bucket_t;

static inline bool maestro_qer_allow(maestro_qer_bucket_t *bucket,
        uint64_t rate_bps, uint64_t now_us, uint32_t bytes)
{
    uint64_t capacity, elapsed, credit, bits = (uint64_t)bytes * 8;
    /* Undefined/unlimited MBR does not install a rate restriction. */
    if (!rate_bps) {
        bucket->initialized = false;
        return true;
    }
    /* Bounded arithmetic; reject unsupported policy rather than overflow. */
    if (rate_bps > UINT64_C(1000000000000)) return false;
    capacity = rate_bps / 20; /* 50 ms nominal burst */
    if (capacity < UINT64_C(65535)*8) capacity = UINT64_C(65535)*8;
    if (!bucket->initialized) {
        bucket->tokens_bits = capacity;
        bucket->fraction = 0;
        bucket->last_us = now_us;
        bucket->rate_bps = rate_bps;
        bucket->initialized = true;
    }
    elapsed = now_us >= bucket->last_us ? now_us-bucket->last_us : 0;
    if (elapsed > 1000000) elapsed = 1000000;
    /* Refill elapsed time at the previous rate, then clamp to new policy. */
    credit = bucket->rate_bps * elapsed + bucket->fraction;
    bucket->tokens_bits += credit / 1000000;
    bucket->fraction = credit % 1000000;
    if (now_us > bucket->last_us) bucket->last_us = now_us;
    bucket->rate_bps = rate_bps;
    if (bucket->tokens_bits > capacity) bucket->tokens_bits = capacity;
    if (bits > bucket->tokens_bits) {
        bucket->dropped_packets++;
        return false;
    }
    bucket->tokens_bits -= bits;
    return true;
}
#endif
