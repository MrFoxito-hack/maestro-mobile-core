/* SPDX-License-Identifier: GPL-2.0 */
#ifndef MAESTRO_C4_ABI_H
#define MAESTRO_C4_ABI_H
#include <linux/types.h>
#include <linux/bpf.h>
#define C4_ABI 1
#define C4_MAGIC 0x43345031U
#define C4_MAX_RATE 1000000000000ULL
#define C4_NS 1000000000ULL
#define C4_CONSUME 0
#define C4_QUIESCE 1
#define C4_SNAPSHOT 2
#define C4_ADMIT 3
#define C4_NATIVE 0
#define C4_XDP 1
#define C4_FALLBACK 0
#define C4_ALLOW 1
#define C4_DENY 2

/* Never use a TEID or recycled UE address as a policy identity. */
struct c4_key {
    __u64 instance, seid, generation;
    __u32 qer_id, urr_id;
};
struct c4_bucket {
    __u64 rate_bps, burst_bits, tokens_bits, last_ns, fraction;
};
struct c4_counter { __u64 packets, bytes; };
struct c4_policy {
    struct bpf_spin_lock lock;
    __u32 abi;
    struct c4_bucket bucket[2];
    /* [path: native/XDP][direction: UL/DL], inner IP octets. */
    struct c4_counter usage[2][2];
    __u64 quota_remaining, lease_ns;
    __u32 finite_quota, urr_directions, gates, admitted;
    __u64 quota_deadline_ns;
};
struct c4_request {
    __u32 magic, abi;
    struct c4_key key;
    __u32 operation, direction, path, bytes;
    struct c4_counter usage[2][2];
    __u64 lease_ns;
};
#endif
