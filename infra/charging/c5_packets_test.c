#include "c5_packets.h"
#include <assert.h>
#include <stdio.h>

int main(void)
{
    smf_sess_t sess = {0};
    smf_chf_sess_t chf = {0};
    ogs_pfcp_volume_measurement_t v = {0};
    char dnn[] = "corporate";
    ogs_core_initialize();
    sess.chf = &chf;
    sess.session.name = dnn;
    sess.s_nssai.sst = 3;
    sess.s_nssai.sd.v = 3;
    unsetenv("MAESTRO_CHF_PACKET_QUOTA");
    assert(!c5_packet_enable(&sess));
    setenv("MAESTRO_CHF_PACKET_QUOTA", "1", 1);
    sess.s_nssai.sst = 2;
    assert(!c5_packet_enable(&sess));
    sess.s_nssai.sst = 3;
    assert(c5_packet_enable(&sess));
    assert(c5_packet_mode(&chf));
    assert(c5_packet_units(&chf, 0) == UINT64_MAX);
    v.tonop = v.ulnop = v.dlnop = 1;
    v.total_n_packets = 3;
    v.uplink_n_packets = 2;
    v.downlink_n_packets = 1;
    v.total_volume = 99999; /* Packet billing never derives from this value. */
    assert(c5_packet_observe(&chf, &v, 0) == OGS_OK);
    assert(c5_packet_units(&chf, 0) == 3);
    assert(c5_packet_observe(&chf, &v, 0) == OGS_OK);
    v.total_n_packets = 4;
    v.uplink_n_packets = 3;
    assert(c5_packet_observe(&chf, &v, 0) == OGS_ERROR);
    assert(chf.uncertain && chf.closing);
    c5_packet_free(&chf);
    assert(!c5_packet_mode(&chf));
    assert(c5_packet_enable(&sess));
    chf.uncertain = chf.closing = false;
    v.ulnop = 0;
    assert(c5_packet_observe(&chf, &v, 1) == OGS_ERROR);
    c5_packet_free(&chf);
    assert(c5_packet_enable(&sess));
    chf.uncertain = chf.closing = false;
    v.ulnop = 1;
    v.uplink_n_packets = UINT64_MAX;
    assert(c5_packet_observe(&chf, &v, 1) == OGS_ERROR);
    c5_packet_free(&chf);
    ogs_core_terminate();
    puts("PASS C5 native: exact slice opt-in, actual packet count, replay, conflict, missing count, overflow and lifetime");
    return 0;
}
