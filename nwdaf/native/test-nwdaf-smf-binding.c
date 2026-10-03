/* Exercise the deployed binding include with isolated Open5GS-shaped fixtures.
 * ASan/UBSan check malformed profiles; sentinels check unrelated QoS preservation.
 */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define OGS_PCC_RULE_TYPE_INSTALL 1
#define OGS_FLOW_DOWNLINK_ONLY 2
#define OGS_PFCP_MODIFY_QOS_MODIFY 4
#define ogs_error(...) ((void)0)
#define ogs_info(...) ((void)0)
typedef struct { uint64_t downlink, uplink; } rate_t;
typedef struct { rate_t mbr, gbr; int fiveqi, arp; } qos_t;
typedef struct { rate_t mbr; unsigned id; } qer_t;
typedef struct { void *far; } pdr_t;
typedef struct { qos_t qos; qer_t *qer; pdr_t *dl_pdr; unsigned qfi; int to_modify_node; } smf_bearer_t;
typedef struct { struct { int sst; struct { unsigned v; } sd; } s_nssai;
    struct { const char *name; } session; int qos_flow_to_modify_list;
    smf_bearer_t *bearer; } sess_t;
typedef struct { int type; const char *id; qos_t qos; int num_of_flow;
    struct { int direction; const char *description; } flow[1]; } rule_t;
static smf_bearer_t *smf_default_bearer_in_sess(sess_t *sess) { return sess->bearer; }
static void ogs_list_add(int *list, int *node) { ++*list; ++*node; }
static int apply(sess_t *sess, rule_t *pcc_rule) {
    int pfcp_flags=0;
    for (int once=0; once<1; once++) {
#include "nwdaf-smf-binding.inc"
    }
    return pfcp_flags;
}
int main(void) {
    int far=42;
    qer_t qer={{1000000000,1234567},1};
    pdr_t pdr={&far};
    smf_bearer_t bearer={.qos={.mbr={1000000000,1234567},.gbr={0,99},.fiveqi=9,.arp=8},
                         .qer=&qer,.dl_pdr=&pdr,.qfi=1};
    sess_t sess={.s_nssai={1,{1}},.session={"internet"},.bearer=&bearer};
    rule_t rule={.type=1,.id="maestro-nwdaf-internet",.qos={.mbr={5000000,20000000}},
                 .num_of_flow=1,.flow={{2,"permit out ip from any to assigned"}}};
    assert(apply(&sess,&rule)==4 && qer.mbr.downlink==5000000);
    assert(bearer.qos.mbr.downlink==5000000 && bearer.qos.mbr.uplink==1234567);
    assert(qer.mbr.uplink==1234567 && bearer.qos.gbr.uplink==99 && bearer.qos.gbr.downlink==0);
    assert(bearer.qos.fiveqi==9 && bearer.qos.arp==8 && pdr.far==&far && bearer.qfi==1);
    assert(apply(&sess,&rule)==0 && sess.qos_flow_to_modify_list==1);
    rule.qos.mbr.downlink=20000000;
    assert(apply(&sess,&rule)==4 && qer.mbr.downlink==20000000);
    for (int invalid=0; invalid<10; invalid++) {
        sess_t s=sess;rule_t r=rule;smf_bearer_t b=bearer;qer_t q=qer;pdr_t p=pdr;
        s.bearer=&b;b.qer=&q;b.dl_pdr=&p;r.qos.mbr.downlink=5000000;
        switch(invalid) {
        case 0:s.s_nssai.sd.v=2;break;
        case 1:s.s_nssai.sst=2;break;
        case 2:s.session.name="corporate";break;
        case 3:s.session.name=NULL;break;
        case 4:r.qos.mbr.downlink=6000000;break;
        case 5:r.num_of_flow=0;break;
        case 6:r.flow[0].description=NULL;break;
        case 7:r.flow[0].direction=1;break;
        case 8:b.qer=NULL;break;
        case 9:p.far=NULL;break;
        }
        assert(apply(&s,&r)==0 && q.mbr.downlink==20000000);
        assert(s.qos_flow_to_modify_list==sess.qos_flow_to_modify_list);
    }
    puts("NWDAF binding: mitigate/restore, idempotence, preserved QoS and 10 rejection paths passed");
    return 0;
}
