/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Runs exclusively on the UPF event thread. Map operations are canonical;
 * failed syscalls never downgrade an enrolled packet to an independent bucket.
 */
#include "context.h"
#include "pfcp-path.h"
#include "c4_bridge.h"
#include "c4_abi.h"
#include <sys/syscall.h>
#include <sys/random.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <inttypes.h>

struct c4_session {
    upf_sess_t *sess;
    struct c4_key key;
    struct c4_counter imported[2];
    struct c4_policy previous;
    bool used, ready, syncing, paused, eligible;
};
static struct c4_session c4_sessions[1024];
static int c4_map = -1, c4_program = -1;
static uint64_t c4_boot, c4_serial;
static ogs_timer_t *c4_timer;
static const char *c4_runtime;
extern void maestro_c4_publish(void);

static uint64_t c4_now(void) { return (uint64_t)ogs_get_monotonic_time() * 1000; }
uint64_t maestro_c4_instance(void) { return c4_boot; }
static struct c4_session *c4_find(upf_sess_t *s)
{
    for (unsigned i=0;i<1024;i++) if (c4_sessions[i].used && c4_sessions[i].sess==s) return &c4_sessions[i];
    return NULL;
}
static int c4_object(const char *name)
{
    char path[4096]; union bpf_attr a={0};
    ogs_assert(snprintf(path,sizeof(path),"%s/%s",getenv("MAESTRO_C4_BPFFS"),name)<sizeof(path));
    a.pathname=(uintptr_t)path;
    return syscall(__NR_bpf,BPF_OBJ_GET,&a,sizeof(a));
}
static int c4_invoke(struct c4_session *e, unsigned op, bool uplink, size_t bytes,
        uint64_t lease, struct c4_request *out)
{
    struct c4_request r={.magic=C4_MAGIC,.abi=C4_ABI,.key=e->key,
        .operation=op,.direction=uplink?0:1,.path=C4_NATIVE,.bytes=bytes,.lease_ns=lease};
    struct c4_request result={0}; union bpf_attr a={0};
    a.test.prog_fd=c4_program;a.test.data_in=(uintptr_t)&r;a.test.data_size_in=sizeof(r);
    a.test.data_out=(uintptr_t)&result;a.test.data_size_out=sizeof(result);a.test.repeat=1;
    if(syscall(__NR_bpf,BPF_PROG_TEST_RUN,&a,sizeof(a))<0) return -1;
    if(out) *out=result;
    return a.test.retval;
}
static void c4_lookup(struct c4_session *e,struct c4_policy *p)
{
    union bpf_attr a={0};a.map_fd=c4_map;a.key=(uintptr_t)&e->key;
    a.value=(uintptr_t)p;a.flags=BPF_F_LOCK;
    ogs_assert(syscall(__NR_bpf,BPF_MAP_LOOKUP_ELEM,&a,sizeof(a))==0);
}
static void c4_checkpoint(struct c4_session *e)
{
    char path[4096],tmp[4096]; FILE *f; int fd;
    ogs_assert(snprintf(path,sizeof(path),"%s/checkpoint-%" PRIu64 "-%" PRIu64 "-%" PRIu64 ".json",
        c4_runtime,(uint64_t)e->key.instance,(uint64_t)e->key.seid,(uint64_t)e->key.generation)<sizeof(path));
    ogs_assert(snprintf(tmp,sizeof(tmp),"%s.tmp",path)<sizeof(tmp));
    fd=open(tmp,O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW|O_CLOEXEC,0600);ogs_assert(fd>=0);
    f=fdopen(fd,"w");ogs_assert(f);
    fprintf(f,"{\"instance\":\"%" PRIu64 "\",\"seid\":\"%" PRIu64 "\",\"generation\":\"%" PRIu64
        "\",\"urr_id\":%u,\"imported\":[[%" PRIu64 ",%" PRIu64 "],[%" PRIu64 ",%" PRIu64 "]],"
        "\"restart_replay_ready\":false}\n",(uint64_t)e->key.instance,(uint64_t)e->key.seid,
        (uint64_t)e->key.generation,e->key.urr_id,(uint64_t)e->imported[0].packets,
        (uint64_t)e->imported[0].bytes,(uint64_t)e->imported[1].packets,(uint64_t)e->imported[1].bytes);
    ogs_assert(!fflush(f));ogs_assert(!fsync(fd));ogs_assert(!fclose(f));ogs_assert(!rename(tmp,path));
}
void maestro_c4_import(upf_sess_t *s)
{
    struct c4_session *e=c4_find(s); struct c4_request r;
    if(!e || !e->ready || e->syncing) return;
    ogs_assert(c4_invoke(e,C4_SNAPSHOT,false,0,0,&r)==XDP_PASS);
    ogs_pfcp_urr_t *u=ogs_pfcp_urr_find(&s->pfcp,e->key.urr_id);
    ogs_assert(u);
    e->syncing=true;
    bool changed=false;
    for(unsigned d=0;d<2;d++) {
        struct c4_counter c=r.usage[C4_XDP][d],old=e->imported[d];
        ogs_assert(c.bytes>=old.bytes && c.packets>=old.packets);
        if(c.packets!=old.packets) {
            ogs_assert(c.bytes>old.bytes);
            /* Advance before report callbacks re-enter import on this event thread. */
            e->imported[d]=c;
            upf_sess_urr_acc_add_many(s,u,c.bytes-old.bytes,c.packets-old.packets,d==0);
            changed=true;
        } else ogs_assert(c.bytes==old.bytes);
    }
    e->syncing=false;
    if(changed) c4_checkpoint(e);
}
bool maestro_c4_managed(upf_sess_t *s,ogs_pfcp_pdr_t *p)
{
    struct c4_session *e=c4_find(s);
    return e && e->ready && p->qer && e->key.qer_id==p->qer->id;
}
bool maestro_c4_native_allow(upf_sess_t *s,ogs_pfcp_pdr_t *p,size_t bytes,bool uplink)
{
    struct c4_session *e=c4_find(s);
    if(!maestro_c4_managed(s,p)) return true;
    maestro_c4_import(s);
    if(e->paused || bytes>65535) return false;
    int result=c4_invoke(e,C4_CONSUME,uplink,bytes,0,NULL);
    ogs_debug("C4 native consume: seid=%" PRIu64 " bytes=%zu uplink=%d result=%d",
        (uint64_t)e->key.seid,bytes,uplink,result);
    return result==XDP_PASS;
}
void maestro_c4_before(upf_sess_t *s)
{
    struct c4_session *e=c4_find(s);
    if(!e || !e->ready) return;
    int rc=c4_invoke(e,C4_QUIESCE,false,0,0,NULL);
    if(rc==XDP_PASS){maestro_c4_import(s);c4_lookup(e,&e->previous);}
    e->paused=true;
}
static bool c4_eligible(upf_sess_t *s,ogs_pfcp_qer_t *q,ogs_pfcp_urr_t *u)
{
    ogs_pfcp_pdr_t *p,*ul=NULL,*dl=NULL;
    if(!s->ipv4 || !s->apn_dnn || strcmp(s->apn_dnn,"5g-plus") || s->ipv6 ||
       q->gbr.uplink || q->gbr.downlink || !q->qfi || q->qfi>63) return false;
    ogs_list_for_each(&s->pfcp.pdr_list,p) {
        if(p->qer!=q) {
            /* CP traffic and exclusively ICMPv6 filters cannot match our IPv4 path. */
            if(p->src_if==OGS_PFCP_INTERFACE_CP_FUNCTION) continue;
            ogs_pfcp_rule_t *rule; bool only_v6=!ogs_list_empty(&p->rule_list);
            ogs_list_for_each(&p->rule_list,rule) if(rule->ipfw.proto!=58) only_v6=false;
            if(!only_v6) return false;
            continue;
        }
        if(p->num_of_flow || !ogs_list_empty(&p->rule_list) || !p->far ||
                p->far->apply_action!=OGS_PFCP_APPLY_ACTION_FORW || p->num_of_urr>1) return false;
        if(p->num_of_urr && p->urr[0]!=u) return false;
        if(p->src_if==OGS_PFCP_INTERFACE_ACCESS) { if(ul)return false;ul=p; }
        else if(p->src_if==OGS_PFCP_INTERFACE_CORE) { if(dl)return false;dl=p; }
        else return false;
    }
    return ul && dl && ul->f_teid.ipv4 && !ul->f_teid.ipv6 &&
        ul->far->dst_if==OGS_PFCP_INTERFACE_CORE && dl->far->dst_if==OGS_PFCP_INTERFACE_ACCESS &&
        dl->far->outer_header_creation.gtpu4 && !dl->far->outer_header_creation.gtpu6 &&
        dl->far->gnode && (!ul->qfi || ul->qfi==q->qfi) && !u->meas_period &&
        !u->event_threshold && !u->event_quota && !u->time_quota && !u->time_threshold &&
        !u->meas_info.octet5 && !u->vol_quota.ulvol && !u->vol_quota.dlvol;
}
void maestro_c4_after(upf_sess_t *s)
{
    if(c4_map<0 || !s) return;
    struct c4_session *e=c4_find(s); ogs_pfcp_qer_t *q=NULL;ogs_pfcp_urr_t *u=NULL;ogs_pfcp_pdr_t *p;
    if(ogs_list_count(&s->pfcp.qer_list)!=1 || ogs_list_count(&s->pfcp.urr_list)!=1) return;
    q=ogs_list_first(&s->pfcp.qer_list);u=ogs_list_first(&s->pfcp.urr_list);
    if(!q->mbr.uplink || !q->mbr.downlink || q->mbr.uplink>C4_MAX_RATE || q->mbr.downlink>C4_MAX_RATE) return;
    if(!e) { for(unsigned i=0;i<1024;i++) if(!c4_sessions[i].used) {e=&c4_sessions[i];break;} }
    ogs_assert(e);
    bool transfer=e->used && e->ready && e->key.qer_id==q->id;
    struct c4_policy previous=e->previous, image={.abi=C4_ABI};
    uint64_t now=c4_now(),rates[2]={q->mbr.uplink,q->mbr.downlink};
    for(unsigned d=0;d<2;d++) {
        uint64_t cap=rates[d]/20;if(cap<65535*8)cap=65535*8;
        image.bucket[d]=(struct c4_bucket){.rate_bps=rates[d],.burst_bits=cap,.last_ns=now};
        if(transfer) {
            struct c4_bucket old=previous.bucket[d];
            uint64_t dt=now>=old.last_ns?now-old.last_ns:0;if(dt>C4_NS)dt=C4_NS;
            uint64_t credit=(old.rate_bps/C4_NS)*dt+((old.rate_bps%C4_NS)*dt+old.fraction)/C4_NS;
            image.bucket[d].tokens_bits=old.tokens_bits+credit;
            if(image.bucket[d].tokens_bits>cap)image.bucket[d].tokens_bits=cap;
        } else if(d==1 && q->maestro_dl_bucket.initialized) {
            image.bucket[d].tokens_bits=q->maestro_dl_bucket.tokens_bits;
            image.bucket[d].last_ns=q->maestro_dl_bucket.last_us*1000;
            image.bucket[d].fraction=q->maestro_dl_bucket.fraction*1000;
            if(image.bucket[d].tokens_bits>cap)image.bucket[d].tokens_bits=cap;
        } /* New directions start empty: no burst can be manufactured by cutover. */
    }
    image.gates=(q->gate_status.uplink?1:0)|(q->gate_status.downlink?2:0);
    ogs_list_for_each(&s->pfcp.pdr_list,p) if(p->qer==q) {
        for(unsigned i=0;i<p->num_of_urr;i++) if(p->urr[i]==u)
            image.urr_directions|=p->src_if==OGS_PFCP_INTERFACE_ACCESS?1:2;
    }
    upf_sess_urr_acc_t *a=&s->urr_acc[u->id-1];
    image.finite_quota=a->quota.active;
    image.quota_remaining=a->quota.limit>a->total_octets?a->quota.limit-a->total_octets:0;
    if(a->quota.expired)image.quota_remaining=0;
    image.quota_deadline_ns=a->quota.deadline>0?(uint64_t)a->quota.deadline*1000:0;
    memset(e,0,sizeof(*e));e->used=true;e->sess=s;
    e->key=(struct c4_key){c4_boot,s->upf_n4_seid,++c4_serial,q->id,u->id};
    union bpf_attr b={0};b.map_fd=c4_map;b.key=(uintptr_t)&e->key;b.value=(uintptr_t)&image;b.flags=BPF_NOEXIST;
    ogs_assert(syscall(__NR_bpf,BPF_MAP_UPDATE_ELEM,&b,sizeof(b))==0);
    e->ready=true;e->eligible=c4_eligible(s,q,u);
    c4_checkpoint(e);
}
void maestro_c4_forget(upf_sess_t *s)
{
    struct c4_session *e=c4_find(s);maestro_c4_before(s);
    if(e)memset(e,0,sizeof(*e)); /* Pins and durable checkpoints are retained. */
}
void maestro_c4_metadata(FILE *f,upf_sess_t *s)
{
    struct c4_session *e=c4_find(s);
    fprintf(f,"\"bridge_ready\":%s,\"fast_eligible\":%s,\"generation\":\"%" PRIu64 "\",",
        e&&e->ready?"true":"false",e&&e->eligible?"true":"false",e?(uint64_t)e->key.generation:0);
}
static void c4_tick(void *unused)
{
    (void)unused;
    for(unsigned i=0;i<1024;i++) {
        struct c4_session *e=&c4_sessions[i];if(!e->used || !e->ready || e->paused)continue;
        maestro_c4_import(e->sess);
        /* Activation protocol is intentionally closed until the complete
         * restart/replay and measurement contract has passed acceptance. */
    }
    ogs_timer_start(c4_timer,ogs_time_from_msec(10));
}
void maestro_c4_open(void)
{
    c4_runtime=getenv("MAESTRO_C4_RUNTIME");if(!c4_runtime)return;
    ogs_assert(getenv("MAESTRO_C4_BPFFS"));
    ogs_assert(getrandom(&c4_boot,sizeof(c4_boot),0)==sizeof(c4_boot) && c4_boot);
    c4_map=c4_object("maps/c4_policy_v1");c4_program=c4_object("policy");ogs_assert(c4_map>=0 && c4_program>=0);
    c4_timer=ogs_timer_add(ogs_app()->timer_mgr,c4_tick,NULL);ogs_assert(c4_timer);
    ogs_timer_start(c4_timer,ogs_time_from_msec(10));
}
void maestro_c4_close(void)
{
    if(c4_map<0)return;
    for(unsigned i=0;i<1024;i++)if(c4_sessions[i].used)maestro_c4_before(c4_sessions[i].sess);
    ogs_timer_delete(c4_timer);c4_timer=NULL;close(c4_map);close(c4_program);c4_map=c4_program=-1;
}
