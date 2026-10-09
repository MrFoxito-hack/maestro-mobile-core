/* SPDX-License-Identifier: GPL-2.0
 * Native syscall integration test: isolated pins only, no NIC attachment. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/udp.h>
#include "c4_abi.h"

static int program, mapfd;
static unsigned tests;
static uint64_t clock_ns(void) {
    struct timespec t;
    assert(clock_gettime(CLOCK_MONOTONIC, &t) == 0);
    return (uint64_t)t.tv_sec * C4_NS + t.tv_nsec;
}
static int bpf_call(int op, union bpf_attr *a) {
    int r = syscall(__NR_bpf, op, a, sizeof(*a));
    if (r < 0) { perror("bpf"); abort(); }
    return r;
}
static int pinned(const char *path) {
    union bpf_attr a = {0};
    a.pathname = (uintptr_t)path;
    return bpf_call(BPF_OBJ_GET, &a);
}
static struct c4_key key(unsigned seid) {
    return (struct c4_key){.instance=98765, .seid=seid, .generation=1, .qer_id=4, .urr_id=7};
}
static struct c4_policy policy(void) {
    struct c4_policy p = {.abi=C4_ABI, .lease_ns=clock_ns()+60*C4_NS,
        .urr_directions=3, .admitted=1};
    for (unsigned d=0; d<2; d++)
        p.bucket[d] = (struct c4_bucket){.rate_bps=C4_MAX_RATE, .burst_bits=C4_MAX_RATE,
            .tokens_bits=C4_MAX_RATE, .last_ns=clock_ns()};
    return p;
}
static void install(struct c4_key k, struct c4_policy p) {
    union bpf_attr a = {0};
    a.map_fd=mapfd; a.key=(uintptr_t)&k; a.value=(uintptr_t)&p; a.flags=BPF_NOEXIST;
    bpf_call(BPF_MAP_UPDATE_ELEM, &a);
}
static int call(struct c4_key k, unsigned op, unsigned path, unsigned dir,
        unsigned bytes, struct c4_request *output) {
    struct c4_request input = {.magic=C4_MAGIC, .abi=C4_ABI, .key=k,
        .operation=op, .path=path, .direction=dir, .bytes=bytes};
    struct c4_request result = {0};
    union bpf_attr a = {0};
    a.test.prog_fd=program; a.test.data_size_in=sizeof(input);
    a.test.data_in=(uintptr_t)&input;
    a.test.data_size_out=sizeof(result); a.test.data_out=(uintptr_t)&result;
    a.test.repeat=1;
    bpf_call(BPF_PROG_TEST_RUN, &a);
    if (output) *output=result;
    return a.test.retval;
}
static uint64_t used(struct c4_request *r, int packets) {
    uint64_t total=0;
    for(unsigned p=0;p<2;p++) for(unsigned d=0;d<2;d++)
        total += packets ? r->usage[p][d].packets : r->usage[p][d].bytes;
    return total;
}
struct worker { unsigned path, direction, accepted; };
static void *concurrent(void *arg) {
    struct worker *w=arg;
    for (unsigned i=0;i<500;i++)
        w->accepted += call(key(20),C4_CONSUME,w->path,w->direction,80,NULL)==XDP_PASS;
    return NULL;
}
struct session {
    uint32_t ue,nat,ul_teid,dl_teid,gnb,upf,n3,n6,mtu;
    uint8_t n3_mac[6],gnb_mac[6],n6_mac[6],gateway_mac[6],qfi,pad[3];
    uint64_t expires_ns;
    struct c4_key policy;
};
static void map_set(int fd, void *k, void *v) {
    union bpf_attr a={0};
    a.map_fd=fd; a.key=(uintptr_t)k; a.value=(uintptr_t)v; a.flags=BPF_NOEXIST;
    bpf_call(BPF_MAP_UPDATE_ELEM,&a);
}
static size_t packet(uint8_t *frame,struct session *s,int ul) {
    memset(frame,0,256);
    struct ethhdr *eth=(void *)frame;
    eth->h_proto=htons(ETH_P_IP);
    struct iphdr *ip=(void *)(eth+1);
    if(ul) {
        ip->version=4; ip->ihl=5; ip->ttl=64; ip->protocol=17;
        ip->tot_len=htons(76); ip->saddr=s->gnb; ip->daddr=s->upf;
        struct udphdr *udp=(void *)(ip+1);
        udp->source=udp->dest=htons(2152); udp->len=htons(56);
        uint8_t *gtp=(void *)(udp+1);
        gtp[0]=0x34; gtp[1]=255; gtp[3]=40;
        memcpy(gtp+4,&s->ul_teid,4);
        gtp[11]=0x85; gtp[12]=1; gtp[13]=0x10; gtp[14]=s->qfi;
        ip=(void *)(gtp+16);
    }
    ip->version=4; ip->ihl=5; ip->ttl=64; ip->protocol=17; ip->tot_len=htons(32);
    ip->saddr=ul?s->ue:inet_addr("172.31.48.2");
    ip->daddr=ul?inet_addr("172.31.48.2"):s->ue;
    struct udphdr *udp=(void *)(ip+1);
    udp->source=htons(ul?5000:8765); udp->dest=htons(ul?8765:5000); udp->len=htons(12);
    return ul?90:46;
}
static int packet_call(int prog,uint8_t *input,size_t len,uint8_t *output,uint32_t *outlen) {
    union bpf_attr a={0};
    struct xdp_md ctx={.data_end=len,.ingress_ifindex=1};
    a.test.prog_fd=prog; a.test.data_in=(uintptr_t)input; a.test.data_size_in=len;
    a.test.data_out=(uintptr_t)output; a.test.data_size_out=256;
    a.test.ctx_in=(uintptr_t)&ctx; a.test.ctx_size_in=sizeof(ctx); a.test.repeat=1;
    bpf_call(BPF_PROG_TEST_RUN,&a);
    *outlen=a.test.data_size_out;
    return a.test.retval;
}
static void packets(const char *root) {
    char path[4096];
    assert(snprintf(path,sizeof(path),"%s/xdp",root)<(int)sizeof(path));
    int prog=pinned(path);
    assert(snprintf(path,sizeof(path),"%s/xmaps/sessions_uplink_map",root)<(int)sizeof(path));
    int ulmap=pinned(path);
    assert(snprintf(path,sizeof(path),"%s/xmaps/sessions_downlink_map",root)<(int)sizeof(path));
    int dlmap=pinned(path);
    assert(snprintf(path,sizeof(path),"%s/xmaps/enabled",root)<(int)sizeof(path));
    int enabled=pinned(path);
    uint32_t zero=0,one=1;
    union bpf_attr a={0}; a.map_fd=enabled;a.key=(uintptr_t)&zero;a.value=(uintptr_t)&one;
    bpf_call(BPF_MAP_UPDATE_ELEM,&a);
    for(unsigned i=0;i<2;i++) {
        struct session s={.ue=inet_addr(i?"10.47.0.3":"10.47.0.2"),
            .ul_teid=htonl(1002+i),.dl_teid=htonl(2002+i),.gnb=inet_addr("10.210.50.3"),
            .upf=inet_addr("10.210.50.22"),.n3=1,.n6=1,.mtu=1400,.qfi=9,
            .expires_ns=clock_ns()+60*C4_NS,.policy=key(102+i)};
        assert(sizeof(s)==104);
        install(s.policy,policy());
        map_set(ulmap,&s.ul_teid,&s);map_set(dlmap,&s.ue,&s);
        uint8_t input[256],output[256];uint32_t outlen;
        for(unsigned d=0;d<2;d++) {
            size_t len=packet(input,&s,!d);
            assert(packet_call(prog,input,len,output,&outlen)==XDP_REDIRECT);
            assert(outlen==(d?90:46));
            struct iphdr *ip=(void *)(output+14+(d?44:0));
            assert(ip->ttl==63 && (d?ip->daddr:ip->saddr)==s.ue);
        }
        struct c4_request r;
        assert(call(s.policy,C4_SNAPSHOT,0,0,0,&r)==XDP_PASS);
        assert(r.usage[C4_XDP][0].bytes==32 && r.usage[C4_XDP][1].bytes==32);
        assert(r.usage[C4_XDP][0].packets==1 && r.usage[C4_XDP][1].packets==1);
        /* Revoked generation falls through unchanged, with no new accounting. */
        assert(call(s.policy,C4_QUIESCE,0,0,0,NULL)==XDP_PASS);
        size_t len=packet(input,&s,1);
        assert(packet_call(prog,input,len,output,&outlen)==XDP_PASS);
        assert(outlen==len && !memcmp(input,output,len));
        assert(call(s.policy,C4_SNAPSHOT,0,0,0,&r)==XDP_PASS && used(&r,0)==64);
    }
    close(prog);close(ulmap);close(dlmap);close(enabled);tests+=3;
}
int main(int argc, char **argv) {
    assert(argc==4);
    program=pinned(argv[1]); mapfd=pinned(argv[2]);
    struct c4_request r, before;
    struct c4_policy p=policy();
    install(key(2),p); install(key(5),p);
    for(unsigned s=2;s<=5;s+=3) for(unsigned d=0;d<2;d++)
        for(unsigned i=0;i<50;i++)
            assert(call(key(s),C4_CONSUME,C4_XDP,d,128,NULL)==XDP_PASS);
    for(unsigned s=2;s<=5;s+=3) {
        assert(call(key(s),C4_SNAPSHOT,0,0,0,&r)==XDP_PASS);
        assert(used(&r,1)==100 && used(&r,0)==12800);
    } tests++;
    struct c4_key recycled=key(2); recycled.generation++;
    assert(call(recycled,C4_CONSUME,C4_XDP,0,128,NULL)==XDP_ABORTED); tests++;
    p=policy(); p.gates=3; install(key(6),p);
    for(unsigned d=0;d<2;d++) for(unsigned path=0;path<2;path++)
        assert(call(key(6),C4_CONSUME,path,d,20,NULL)==XDP_DROP);
    tests++;
    p=policy(); p.lease_ns=1; install(key(7),p);
    assert(call(key(7),C4_CONSUME,C4_XDP,0,20,NULL)==XDP_ABORTED);
    assert(call(key(7),C4_CONSUME,C4_NATIVE,0,20,NULL)==XDP_PASS); tests++;
    assert(call(key(2),C4_QUIESCE,0,0,0,&before)==XDP_PASS);
    assert(call(key(2),C4_CONSUME,C4_XDP,0,20,NULL)==XDP_ABORTED);
    assert(call(key(2),C4_SNAPSHOT,0,0,0,&r)==XDP_PASS);
    assert(!memcmp(r.usage,before.usage,sizeof(r.usage)));
    assert(call(key(2),C4_CONSUME,C4_NATIVE,0,20,NULL)==XDP_PASS); tests++;
    p=policy(); p.finite_quota=1; p.quota_remaining=800; install(key(20),p);
    pthread_t threads[8]; struct worker workers[8]={0};
    for(unsigned i=0;i<8;i++) {
        workers[i].path=i%2; workers[i].direction=(i/2)%2;
        assert(!pthread_create(&threads[i],NULL,concurrent,&workers[i]));
    }
    unsigned accepted=0;
    for(unsigned i=0;i<8;i++) { assert(!pthread_join(threads[i],NULL)); accepted+=workers[i].accepted; }
    assert(accepted==10);
    assert(call(key(20),C4_SNAPSHOT,0,0,0,&r)==XDP_PASS);
    assert(used(&r,0)==800 && used(&r,1)==10); tests++;
    p=policy(); p.finite_quota=1; p.quota_remaining=0; p.urr_directions=1; install(key(21),p);
    assert(call(key(21),C4_CONSUME,C4_XDP,0,20,NULL)==XDP_DROP);
    assert(call(key(21),C4_CONSUME,C4_XDP,1,20,NULL)==XDP_PASS);
    assert(call(key(21),C4_SNAPSHOT,0,0,0,&r)==XDP_PASS && used(&r,0)==0); tests++;
    p=policy(); p.usage[C4_XDP][0].bytes=UINT64_MAX; install(key(22),p);
    assert(call(key(22),C4_CONSUME,C4_XDP,0,20,NULL)==XDP_DROP); tests++;
    p=policy();
    for(unsigned d=0;d<2;d++) p.bucket[d]=(struct c4_bucket){.rate_bps=800,
        .burst_bits=800,.tokens_bits=800,.last_ns=clock_ns()};
    install(key(30),p);
    uint64_t start=clock_ns(), bytes=0;
    for(unsigned i=0;i<1000;i++)
        if(call(key(30),C4_CONSUME,i%2,0,100,NULL)==XDP_PASS) bytes+=100;
    uint64_t duration=clock_ns()-start;
    assert(bytes>=100 && bytes<=100+800*duration/(8*C4_NS)); tests++;
    /* Quiescing must not refill the native path with a second full burst. */
    assert(call(key(30),C4_QUIESCE,0,0,0,NULL)==XDP_PASS);
    int after=call(key(30),C4_CONSUME,C4_NATIVE,0,100,NULL);
    if(clock_ns()-start<C4_NS) assert(after==XDP_DROP);
    tests++;
    p=policy(); install(key(40),p);
    start=clock_ns();
    for(unsigned i=0;i<10000;i++)
        assert(call(key(40),C4_CONSUME,C4_NATIVE,0,100,NULL)==XDP_PASS);
    uint64_t benchmark=clock_ns()-start;
    packets(argv[3]);
    printf("{\"tests\":%u,\"two_sessions\":true,\"concurrent_calls\":4000,"
        "\"quota_accepted_packets\":%u,\"qer_bytes\":%llu,\"qer_duration_ns\":%llu,"
        "\"syscall_average_ns\":%llu,\"attached\":false}\n", tests,accepted,
        (unsigned long long)bytes,(unsigned long long)duration,(unsigned long long)(benchmark/10000));
    close(program); close(mapfd);
    return 0;
}
