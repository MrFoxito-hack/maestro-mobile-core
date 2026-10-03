// SPDX-License-Identifier: GPL-2.0
// Experimental IPv4 UPF fast path; unknown sessions retain the Open5GS path.
#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/udp.h>
#include <linux/tcp.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>

struct gtpv1_hdr { __u8 flags, type; __be16 length; __be32 teid; };
struct session {
    __be32 ue, nat, ul_teid, dl_teid, gnb, upf;
    __u32 n3, n6, mtu;
    __u8 n3_mac[6], gnb_mac[6], n6_mac[6], gateway_mac[6];
    __u8 qfi, pad[3];
    __u64 expires_ns;
};
#define SESSION_MAP(name) struct { __uint(type, BPF_MAP_TYPE_HASH); \
    __uint(max_entries, 1024); __type(key, __be32); \
    __type(value, struct session); } name SEC(".maps")
SESSION_MAP(sessions_uplink_map);
SESSION_MAP(sessions_downlink_map);
SESSION_MAP(nat_downlink_map);
struct counter { __u64 packets, bytes; };
enum { UL_OK, DL_OK, PASS, UNKNOWN, INVALID, ADJUST_FAIL, EXPIRED, UNSUPPORTED, COUNT };
struct { __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY); __uint(max_entries, COUNT);
    __type(key, __u32); __type(value, struct counter); } counters SEC(".maps");
struct { __uint(type, BPF_MAP_TYPE_ARRAY); __uint(max_entries, 1);
    __type(key, __u32); __type(value, __u32); } enabled SEC(".maps");

static __always_inline int count(struct xdp_md *ctx, __u32 reason, int action) {
    struct counter *c = bpf_map_lookup_elem(&counters, &reason);
    if (c) { c->packets++; c->bytes += ctx->data_end - ctx->data; }
    return action;
}
static __always_inline __be16 fold(__u32 sum) {
    sum = (sum & 65535) + (sum >> 16);
    sum = (sum & 65535) + (sum >> 16);
    return bpf_htons(~sum);
}
static __always_inline __be16 checksum(struct iphdr *ip) {
    __u16 *p = (void *)ip;
    __u32 sum = 0;
    #pragma unroll
    for (int i = 0; i < 10; i++) sum += bpf_ntohs(p[i]);
    return fold(sum);
}
static __always_inline int supported(struct iphdr *ip, void *end) {
    if ((void *)(ip + 1) > end || ip->version != 4 || ip->ihl != 5 ||
        (ip->frag_off & bpf_htons(0x3fff)) || ip->ttl <= 1) return 0;
    __u32 len = bpf_ntohs(ip->tot_len);
    if (len < 20 || len > 1500) return 0;
    if ((void *)ip + len > end) return 0;
    if (ip->protocol == 6) return len >= 40 && (void *)(ip + 1) + 20 <= end;
    if (ip->protocol == 17) return len >= 28 && (void *)(ip + 1) + 8 <= end;
    // Only ICMP echo; ICMP errors need translation of the embedded packet.
    if (ip->protocol == 1 && len >= 28 && (void *)(ip + 1) + 8 <= end) {
        __u8 type = *(__u8 *)(ip + 1);
        return type == 0 || type == 8;
    }
    return 0;
}
static __always_inline void rewrite(struct iphdr *ip, void *end, __be32 old, __be32 new) {
    __be16 *check = 0;
    if (ip->protocol == 6) {
        struct tcphdr *tcp = (void *)(ip + 1);
        if ((void *)(tcp + 1) <= end) check = &tcp->check;
    } else if (ip->protocol == 17) {
        struct udphdr *udp = (void *)(ip + 1);
        if ((void *)(udp + 1) <= end && udp->check) check = &udp->check;
    }
    if (check && old != new) {
        __u32 a = bpf_ntohl(old), b = bpf_ntohl(new);
        __u32 sum = (~bpf_ntohs(*check) & 65535) + (~(a >> 16) & 65535) +
            (~a & 65535) + (b >> 16) + (b & 65535);
        *check = fold(sum);
        if (!*check) *check = 0xffff;
    }
    ip->ttl--;
    ip->check = 0;
    ip->check = checksum(ip);
}

SEC("xdp") int xdp_upf(struct xdp_md *ctx) {
    void *data = (void *)(long)ctx->data, *end = (void *)(long)ctx->data_end;
    struct ethhdr *eth = data;
    __u32 zero = 0, *active = bpf_map_lookup_elem(&enabled, &zero);
    if (!active || !*active) return XDP_PASS;
    if ((void *)(eth + 1) > end || eth->h_proto != bpf_htons(ETH_P_IP))
        return count(ctx, PASS, XDP_PASS);
    struct iphdr *ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > end || ip->ihl < 5) return count(ctx, INVALID, XDP_PASS);
    __u32 ihl = ip->ihl * 4;
    struct udphdr *udp = (void *)ip + ihl;
    if (ip->protocol == 17 && (void *)(udp + 1) <= end && udp->dest == bpf_htons(2152)) {
        struct gtpv1_hdr *gtp = (void *)(udp + 1);
        if ((ip->frag_off & bpf_htons(0x3fff)) || (void *)(gtp + 1) > end ||
            (gtp->flags & 0xf8) != 0x30 || gtp->type != 255) return count(ctx, PASS, XDP_PASS);
        __be32 key = gtp->teid;
        struct session *s = bpf_map_lookup_elem(&sessions_uplink_map, &key);
        if (!s) return count(ctx, UNKNOWN, XDP_PASS);
        if (bpf_ktime_get_ns() > s->expires_ns) return count(ctx, EXPIRED, XDP_PASS);
        if (ctx->ingress_ifindex != s->n3 || ip->saddr != s->gnb || ip->daddr != s->upf)
            return count(ctx, INVALID, XDP_PASS);
        __u32 glen = bpf_ntohs(gtp->length), off = 8;
        if (bpf_ntohs(udp->len) != 16 + glen || bpf_ntohs(ip->tot_len) != ihl + 16 + glen)
            return count(ctx, INVALID, XDP_PASS);
        __u8 next = 0;
        if (gtp->flags & 7) {
            __u8 *opt = (void *)(gtp + 1);
            if (opt + 4 > (__u8 *)end) return count(ctx, INVALID, XDP_PASS);
            next = opt[3]; off += 4;
            if (!(gtp->flags & 4) && next) return count(ctx, INVALID, XDP_PASS);
        }
        #pragma unroll
        for (int i = 0; i < 4; i++) {
            if (!next) break;
            __u8 *ext = (void *)gtp + off;
            if (ext + 1 > (__u8 *)end) return count(ctx, INVALID, XDP_PASS);
            __u32 size = ext[0] * 4;
            if (size < 4 || ext + size > (__u8 *)end) return count(ctx, INVALID, XDP_PASS);
            __u32 last_offset = (size - 1) & 1023;
            __u8 *last = ext + last_offset;
            if (last + 1 > (__u8 *)end) return count(ctx, INVALID, XDP_PASS);
            next = *last; off += size;
        }
        if (next || off > 264 || off > glen + 8) return count(ctx, UNSUPPORTED, XDP_PASS);
        struct iphdr *inner = (void *)gtp + off;
        if (!supported(inner, end)) return count(ctx, UNSUPPORTED, XDP_PASS);
        if (inner->saddr != s->ue || bpf_ntohs(inner->tot_len) != glen + 8 - off)
            return count(ctx, INVALID, XDP_PASS);
        // Local/control destinations stay in Open5GS. The fast path uses N6's default gateway.
        __u32 dest = bpf_ntohl(inner->daddr);
        if ((dest & 0xffff0000) == 0x0a2d0000 || (dest & 0xffffff00) == 0x0ad23200 ||
            bpf_ntohs(inner->tot_len) > s->mtu) return count(ctx, UNSUPPORTED, XDP_PASS);
        struct session copy = *s;
        if (bpf_xdp_adjust_head(ctx, ihl + 8 + off)) return count(ctx, ADJUST_FAIL, XDP_DROP);
        data = (void *)(long)ctx->data; end = (void *)(long)ctx->data_end;
        eth = data; ip = (void *)(eth + 1);
        if ((void *)(ip + 1) > end) return XDP_DROP;
        __builtin_memcpy(eth->h_source, copy.n6_mac, 6);
        __builtin_memcpy(eth->h_dest, copy.gateway_mac, 6);
        eth->h_proto = bpf_htons(ETH_P_IP);
        __be32 old = ip->saddr, addr = copy.nat ? copy.nat : old;
        ip->saddr = addr;
        rewrite(ip, end, old, addr);
        count(ctx, UL_OK, XDP_REDIRECT);
        return bpf_redirect(copy.n6, 0);
    }
    __be32 key = ip->daddr;
    struct session *s = bpf_map_lookup_elem(&sessions_downlink_map, &key);
    if (!s) s = bpf_map_lookup_elem(&nat_downlink_map, &key);
    if (!s || ctx->ingress_ifindex != s->n6) return count(ctx, PASS, XDP_PASS);
    if (bpf_ktime_get_ns() > s->expires_ns) return count(ctx, EXPIRED, XDP_PASS);
    if (!supported(ip, end) || bpf_ntohs(ip->tot_len) + 44 > s->mtu)
        return count(ctx, UNSUPPORTED, XDP_PASS);
    struct session copy = *s;
    __u16 len = bpf_ntohs(ip->tot_len);
    __be32 old = ip->daddr;
    ip->daddr = copy.ue; rewrite(ip, end, old, copy.ue);
    if (bpf_xdp_adjust_head(ctx, -44)) return count(ctx, ADJUST_FAIL, XDP_DROP);
    data = (void *)(long)ctx->data; end = (void *)(long)ctx->data_end;
    eth = data; ip = (void *)(eth + 1); udp = (void *)(ip + 1);
    struct gtpv1_hdr *gtp = (void *)(udp + 1);
    __u8 *opt = (void *)(gtp + 1);
    if (opt + 8 > (__u8 *)end) return XDP_DROP;
    __builtin_memcpy(eth->h_source, copy.n3_mac, 6);
    __builtin_memcpy(eth->h_dest, copy.gnb_mac, 6);
    eth->h_proto = bpf_htons(ETH_P_IP);
    __builtin_memset(ip, 0, sizeof(*ip));
    ip->version = 4; ip->ihl = 5; ip->ttl = 64; ip->protocol = 17;
    ip->tot_len = bpf_htons(len + 44); ip->frag_off = bpf_htons(0x4000);
    ip->saddr = copy.upf; ip->daddr = copy.gnb; ip->check = checksum(ip);
    udp->source = bpf_htons(2152); udp->dest = bpf_htons(2152);
    udp->len = bpf_htons(len + 24); udp->check = 0;
    gtp->flags = 0x34; gtp->type = 255; gtp->length = bpf_htons(len + 8); gtp->teid = copy.dl_teid;
    opt[0] = 0; opt[1] = 0; opt[2] = 0; opt[3] = 0x85;
    opt[4] = 1; opt[5] = 0; opt[6] = copy.qfi & 63; opt[7] = 0;
    count(ctx, DL_OK, XDP_REDIRECT);
    return bpf_redirect(copy.n3, 0);
}
char LICENSE[] SEC("license") = "GPL";
