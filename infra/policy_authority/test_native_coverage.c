/* SPDX-License-Identifier: AGPL-3.0-or-later */
#define _GNU_SOURCE
#define MAESTRO_NATIVE_COVERAGE_V1 1
#include "native_coverage.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
static uint64_t now = 100;
static uint64_t clock_ns(void) { return now; }
int main(void)
{
    char dir[] = "/tmp/maestro-coverage-XXXXXX", path[256];
    assert(!mcv_fenced() && !mcv_complete(0,0,true));
    assert(mkdtemp(dir) && !mfc_open(dir,clock_ns));
    assert(!mcv_fenced());
    assert(!mf_fence(&maestro_native_gate,1,"01234567-89ab-cdef-0123-456789abcdef",1000));
    assert(mcv_fenced() && mcv_complete(0,0,true));
    assert(!mcv_complete(1,0,true) && !mcv_complete(0,1,true));
    assert(!mcv_complete(0,0,false));
    maestro_native_gate.recovery_required=true;
    assert(!mcv_fenced()); maestro_native_gate.recovery_required=false;
    maestro_native_gate.failed=true;
    assert(!mcv_fenced()); maestro_native_gate.failed=false;
    now+=1001; assert(!mcv_fenced() && !mcv_complete(0,0,true));
    mfc_close();
    snprintf(path,sizeof(path),"%s/fence.state",dir);assert(!unlink(path));
    snprintf(path,sizeof(path),"%s/fence.lock",dir);assert(!unlink(path));
    assert(!rmdir(dir));
    puts("PASS native coverage: disabled, bootstrap, active, pending, deferred, unsupported, recovery, failure, expiry");
    return 0;
}
