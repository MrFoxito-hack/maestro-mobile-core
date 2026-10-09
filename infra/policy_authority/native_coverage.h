/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Coverage is a build contract, plus a live gate/quietness contract. The build
 * macro is emitted only after every exact-source boundary patch has succeeded.
 * This predicate alone cannot establish source coverage.
 */
#ifndef MAESTRO_NATIVE_COVERAGE_H
#define MAESTRO_NATIVE_COVERAGE_H
#include "native_control.h"
#include "native_completion.h"
#ifndef MAESTRO_NATIVE_COVERAGE_V1
#define MAESTRO_NATIVE_COVERAGE_V1 0
#endif
static inline bool mcv_fenced(void)
{
    return MAESTRO_NATIVE_COVERAGE_V1 == 1 && mfc_enabled() &&
        maestro_native_gate.token != 0 && !maestro_native_gate.failed &&
        !maestro_native_gate.recovery_required && mf_leased(&maestro_native_gate);
}
static inline bool mcv_complete(unsigned pending, unsigned deferred, bool profile)
{
    return mcv_fenced() && profile && !pending && !deferred &&
        !mnc_pending() && !mnc_pending_n4();
}
#endif
