/* SPDX-License-Identifier: AGPL-3.0-or-later */
#include "qer-policer.h"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdio.h>

int main(void)
{
    maestro_qer_bucket_t first = {0}, second = {0};
    uint64_t t, accepted = 0, before;
    for (t=0;t<10000000;t+=100) {
        if (maestro_qer_allow(&first,5000000,t,1400)) accepted += 1400*8;
    }
    assert(accepted <= 5000000*UINT64_C(10)+65535*8);
    assert(accepted >= 4900000*UINT64_C(10));
    assert(first.dropped_packets > 0);
    assert(second.dropped_packets == 0);
    /* Independent QER and zero/unlimited rate. */
    assert(maestro_qer_allow(&second,0,0,65535));
    assert(maestro_qer_allow(&second,5000000,0,65535));
    assert(!maestro_qer_allow(&second,5000000,0,1400));
    /* Recovery does not keep the old rate; backwards time grants no credit. */
    assert(maestro_qer_allow(&second,20000000,1000000,1400));
    before = second.tokens_bits;
    assert(maestro_qer_allow(&second,20000000,999999,1400));
    assert(second.tokens_bits == before-1400*8);
    assert(second.last_us == 1000000);
    before = second.tokens_bits;
    assert(maestro_qer_allow(&second,20000000,1000000,1400));
    assert(second.tokens_bits == before-1400*8);
    assert(!maestro_qer_allow(&second,UINT64_MAX,1000001,1400));
    puts("QER policer arithmetic, isolation, rate update and clock tests passed");
    return 0;
}
