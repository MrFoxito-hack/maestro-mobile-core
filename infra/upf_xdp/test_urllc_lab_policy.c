#include <assert.h>
#include "urllc_lab_policy.h"
int main(void)
{
    const char *ue = "imsi-999700000000002";
    assert(maestro_urllc_profile_allowed("1", false, false, false, ue, "5g-plus", 2, 2));
    assert(!maestro_urllc_profile_allowed(NULL, false, false, false, ue, "5g-plus", 2, 2));
    assert(!maestro_urllc_profile_allowed("0", false, false, false, ue, "5g-plus", 2, 2));
    assert(!maestro_urllc_profile_allowed("1", true, false, false, ue, "5g-plus", 2, 2));
    assert(!maestro_urllc_profile_allowed("1", false, true, false, ue, "5g-plus", 2, 2));
    assert(!maestro_urllc_profile_allowed("1", false, false, true, ue, "5g-plus", 2, 2));
    assert(!maestro_urllc_profile_allowed("1", false, false, false, "imsi-999700000000001", "5g-plus", 2, 2));
    assert(!maestro_urllc_profile_allowed("1", false, false, false, "imsi-999700000000003", "corporate", 3, 3));
    assert(!maestro_urllc_profile_allowed("1", false, false, false, ue, "internet", 2, 2));
    assert(!maestro_urllc_profile_allowed("1", false, false, false, ue, "5g-plus", 1, 2));
    assert(!maestro_urllc_profile_allowed("1", false, false, false, ue, "5g-plus", 2, 1));
    assert(!maestro_urllc_profile_allowed("1", false, false, false, NULL, "5g-plus", 2, 2));
    assert(!maestro_urllc_profile_allowed("1", false, false, false, ue, NULL, 2, 2));
    return 0;
}
