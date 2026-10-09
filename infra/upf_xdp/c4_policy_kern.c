/* SPDX-License-Identifier: GPL-2.0 */
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>
#include "c4_policy_bpf.h"

/* Privileged BPF_PROG_TEST_RUN endpoint. Do not attach this program to a NIC.
 * It shares the map used by the C4 packet program (map reuse is mandatory).
 * XDP_PASS = accepted, XDP_DROP = denied, XDP_ABORTED = unavailable. */
SEC("xdp") int c4_policy_call(struct xdp_md *ctx)
{
    void *data = (void *)(long)ctx->data;
    void *end = (void *)(long)ctx->data_end;
    struct c4_request *r = data;
    if ((void *)(r + 1) > end || r->magic != C4_MAGIC || r->abi != C4_ABI)
        return XDP_ABORTED;
    struct c4_key key = r->key;
    if (r->operation == C4_CONSUME) {
        int result = c4_consume(&key, r->direction, r->path, r->bytes);
        return result == C4_ALLOW ? XDP_PASS :
            result == C4_DENY ? XDP_DROP : XDP_ABORTED;
    }
    if (r->operation != C4_QUIESCE && r->operation != C4_SNAPSHOT && r->operation != C4_ADMIT)
        return XDP_ABORTED;
    __u64 now = bpf_ktime_get_ns();
    if (r->operation == C4_ADMIT && (r->lease_ns <= now || r->lease_ns - now > 3 * C4_NS))
        return XDP_ABORTED;
    struct c4_policy *p = bpf_map_lookup_elem(&c4_policy_v1, &key);
    if (!p) return XDP_ABORTED;
    bpf_spin_lock(&p->lock);
    if (r->operation == C4_QUIESCE) p->admitted = 0;
    if (r->operation == C4_ADMIT) {
        p->lease_ns = r->lease_ns;
        p->admitted = 1;
    }
    __builtin_memcpy(r->usage, p->usage, sizeof(r->usage));
    bpf_spin_unlock(&p->lock);
    return XDP_PASS;
}
char LICENSE[] SEC("license") = "GPL";
