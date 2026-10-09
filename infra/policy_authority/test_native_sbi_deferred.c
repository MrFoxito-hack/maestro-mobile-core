/* SPDX-License-Identifier: AGPL-3.0-or-later */
#define _GNU_SOURCE
#include "context.h"
#include "native_sbi_deferred.h"
#include <assert.h>
#include <stdlib.h>
#include <unistd.h>
int __smf_log_domain;
static ogs_sbi_xact_t xact;
static unsigned disposed, responses;
static uint64_t now=100;
static uint64_t clock_ns(void) { return now; }
ogs_sbi_xact_t *ogs_sbi_xact_find_by_id(ogs_pool_id_t id)
{ return id==xact.id ? &xact : NULL; }
void ogs_event_free(void *e) { disposed++;free(e); }
void ogs_sbi_response_free(ogs_sbi_response_t *r) { responses++;free(r); }
static ogs_event_t *make(int type)
{
    ogs_event_t *e=calloc(1,sizeof(*e));assert(e);e->id=type;
    e->sbi.data=OGS_UINT_TO_POINTER(7);
    if(type==OGS_EVENT_SBI_CLIENT) e->sbi.response=calloc(1,sizeof(ogs_sbi_response_t));
    else e->timer_id=OGS_TIMER_SBI_CLIENT_WAIT;
    return e;
}
int main(void)
{
    char dir[]="/tmp/maestro-sbi-deferred-XXXXXX",path[256];
    const char *boot="01234567-89ab-cdef-0123-456789abcdef";
    unsigned char cookie[MF_COOKIE_BYTES]={1};
    ogs_event_t *a,*b;
    xact.id=7;xact.sbi_object_id=8;
    assert(mkdtemp(dir) && !mfc_open(dir,clock_ns));
    assert(!mf_fence(&maestro_native_gate,1,boot,1000));
    a=make(OGS_EVENT_SBI_CLIENT);b=make(OGS_EVENT_SBI_TIMER);
    assert(nsd_hold(a) && nsd_hold(b) && nsd_count()==2 && !nsd_take());
    { ogs_event_t nrf={0};nrf.id=OGS_EVENT_SBI_CLIENT;nrf.sbi.data=&nrf;
      assert(!nsd_hold(&nrf)); }
    assert(!mf_prepare_system(&maestro_native_gate,1,0,cookie));
    assert(nsd_take()==a);nsd_dispose(a);
    assert(nsd_take()==b);nsd_dispose(b);
    assert(!nsd_take() && responses==1 && disposed==2);
    now+=1001;a=make(OGS_EVENT_SBI_CLIENT);assert(nsd_hold(a));
    xact.sbi_object_id=99; /* Never deliver to a recycled transaction owner. */
    assert(!mf_fence(&maestro_native_gate,2,boot,1000));
    assert(!mf_prepare_system(&maestro_native_gate,2,0,cookie));
    assert(!nsd_take() && responses==2 && disposed==3);
    now+=1001;assert(nsd_hold(make(OGS_EVENT_SBI_CLIENT)));nsd_close();
    assert(responses==3 && disposed==4 && !nsd_count());mfc_close();
    snprintf(path,sizeof(path),"%s/fence.state",dir);assert(!unlink(path));
    snprintf(path,sizeof(path),"%s/fence.lock",dir);assert(!unlink(path));assert(!rmdir(dir));
    puts("PASS SBI deferred: late response/timer FIFO, expiry, owner identity, NRF pass-through, disposal");
    return 0;
}
