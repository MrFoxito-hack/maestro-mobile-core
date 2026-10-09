/* C5 sidecar: preserves the ABI of the frozen native policy components. */
#ifndef MAESTRO_C5_PACKETS_H
#define MAESTRO_C5_PACKETS_H
#include "chf-path.h"
bool c5_packet_enable(smf_sess_t *sess);
bool c5_packet_mode(smf_chf_sess_t *chf);
void c5_packet_free(smf_chf_sess_t *chf);
int c5_packet_observe(smf_chf_sess_t *chf,
        const ogs_pfcp_volume_measurement_t *volume, uint32_t sequence);
uint64_t c5_packet_units(smf_chf_sess_t *chf, uint32_t sequence);
int c5_packet_urr(smf_sess_t *sess, smf_bearer_t *bearer);
#endif
