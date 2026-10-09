/* SPDX-License-Identifier: GPL-2.0 */
#ifndef MAESTRO_C4_POLICY_BPF_H
#define MAESTRO_C4_POLICY_BPF_H
#include "c4_abi.h"
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 1024);
    __type(key, struct c4_key);
    __type(value, struct c4_policy);
} c4_policy_v1 SEC(".maps");

/* One lock owns both QER directions and the shared URR quota. No userspace
 * read/modify/write is permitted after BPF_NOEXIST creates a generation. */
static __always_inline int c4_consume(struct c4_key *key, __u32 direction,
        __u32 path, __u32 bytes)
{
    if (direction > 1 || path > 1 || bytes < 20 || bytes > 65535)
        return C4_DENY;
    /* clang folds the two range checks into an OR; Linux 5.15 cannot recover
     * individual index bounds from it. Keep explicit bounded indices. */
    asm volatile("" : "+r"(direction), "+r"(path));
    direction &= 1;
    path &= 1;
    struct c4_policy *p = bpf_map_lookup_elem(&c4_policy_v1, key);
    if (!p) return C4_FALLBACK;
    __u64 now = bpf_ktime_get_ns();
    int result = C4_DENY;
    bpf_spin_lock(&p->lock);
    if (p->abi != C4_ABI) goto done;
    if (path == C4_XDP && (!p->admitted || now >= p->lease_ns)) {
        result = C4_FALLBACK;
        goto done;
    }
    if (p->gates & (1U << direction)) goto done;
    struct c4_bucket *b = &p->bucket[direction];
    if (!b->rate_bps || b->rate_bps > C4_MAX_RATE ||
            !b->burst_bits || b->burst_bits > C4_MAX_RATE ||
            b->tokens_bits > b->burst_bits || b->fraction >= C4_NS)
        goto done;
    /* Concurrent callers may acquire the lock in a different order from
     * their clock reads. Never mint negative-time credit or spuriously drop. */
    if (now < b->last_ns) now = b->last_ns;
    __u64 elapsed = now - b->last_ns;
    if (elapsed >= C4_NS) {
        b->tokens_bits = b->burst_bits;
        b->fraction = 0;
    } else {
        __u64 fractional = (b->rate_bps % C4_NS) * elapsed + b->fraction;
        __u64 added = (b->rate_bps / C4_NS) * elapsed + fractional / C4_NS;
        b->fraction = fractional % C4_NS;
        if (added >= b->burst_bits - b->tokens_bits) {
            b->tokens_bits = b->burst_bits;
            b->fraction = 0;
        } else b->tokens_bits += added;
    }
    b->last_ns = now;
    __u64 cost = (__u64)bytes * 8;
    if (b->tokens_bits < cost) goto done;
    int metered = !!(p->urr_directions & (1U << direction));
    if (metered && p->finite_quota && (p->quota_remaining < bytes ||
            (p->quota_deadline_ns && now >= p->quota_deadline_ns))) goto done;
    struct c4_counter *c = &p->usage[path][direction];
    if (metered && (c->packets == ~0ULL || c->bytes > ~0ULL - bytes)) goto done;
    b->tokens_bits -= cost;
    if (metered) {
        if (p->finite_quota) p->quota_remaining -= bytes;
        c->packets++;
        c->bytes += bytes;
    }
    result = C4_ALLOW;
done:
    bpf_spin_unlock(&p->lock);
    return result;
}
#endif
