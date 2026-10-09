/* SPDX-License-Identifier: AGPL-3.0-or-later */
#ifndef MAESTRO_URLLC_LAB_POLICY_H
#define MAESTRO_URLLC_LAB_POLICY_H
#include <stdbool.h>
#include <string.h>

/* Explicit opt-in for one laboratory subscription. SST alone is insufficient. */
static inline bool maestro_urllc_profile_allowed(const char *opt_in,
        bool charging, bool epc, bool roaming, const char *supi,
        const char *dnn, unsigned int sst, unsigned int sd)
{
    return opt_in && !strcmp(opt_in, "1") && !charging && !epc && !roaming &&
        supi && !strcmp(supi, "imsi-999700000000002") &&
        dnn && !strcmp(dnn, "5g-plus") && sst == 2 && sd == 2;
}
#endif
