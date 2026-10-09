/* Native PFCP packet-count charging. No estimate from octets; no BPF changes.
 * All calls are on the SMF event loop. Lifetime follows the CHF owner refs.
 * Credit is settled at PFCP reports, with explicit overrun retained by CHF.
 */
#include "c5_packets.h"

typedef struct {
    bool present;
    uint32_t sequence;
    uint64_t ul, dl;
} c5_count_t;
typedef struct c5_owner_s {
    struct c5_owner_s *next;
    smf_chf_sess_t *owner;
    c5_count_t reports[SMF_CHF_MAX_REPORTS];
} c5_owner_t;
static c5_owner_t *owners;

static c5_owner_t *find(smf_chf_sess_t *chf)
{
    c5_owner_t *item;
    for (item = owners; item; item = item->next)
        if (item->owner == chf) return item;
    return NULL;
}

bool c5_packet_enable(smf_sess_t *sess)
{
    c5_owner_t *item;
    const char *flag = getenv("MAESTRO_CHF_PACKET_QUOTA");
    if (!flag || strcmp(flag, "1") || !sess->chf || sess->epc ||
            !sess->session.name || strcmp(sess->session.name, "corporate") ||
            sess->s_nssai.sst != 3 || sess->s_nssai.sd.v != 3) return false;
    if (find(sess->chf)) return true;
    item = ogs_calloc(1, sizeof(*item));
    ogs_assert(item);
    item->owner = sess->chf;
    item->next = owners;
    owners = item;
    return true;
}

bool c5_packet_mode(smf_chf_sess_t *chf) { return find(chf) != NULL; }

void c5_packet_free(smf_chf_sess_t *chf)
{
    c5_owner_t **item;
    for (item = &owners; *item; item = &(*item)->next) {
        if ((*item)->owner == chf) {
            c5_owner_t *old = *item;
            *item = old->next;
            ogs_free(old);
            return;
        }
    }
}

int c5_packet_observe(smf_chf_sess_t *chf,
        const ogs_pfcp_volume_measurement_t *v, uint32_t sequence)
{
    c5_owner_t *owner = find(chf);
    c5_count_t *count;
    if (!owner) return OGS_OK;
    if (!v->ulnop || !v->dlnop || !v->tonop ||
            v->uplink_n_packets > 9007199254740991ULL ||
            v->downlink_n_packets > 9007199254740991ULL - v->uplink_n_packets ||
            v->total_n_packets != v->uplink_n_packets + v->downlink_n_packets)
        goto invalid;
    count = &owner->reports[sequence % SMF_CHF_MAX_REPORTS];
    if (count->present && (count->sequence > sequence ||
            (count->sequence == sequence && (count->ul != v->uplink_n_packets ||
             count->dl != v->downlink_n_packets)))) goto invalid;
    *count = (c5_count_t){true, sequence, v->uplink_n_packets, v->downlink_n_packets};
    return OGS_OK;
invalid:
    chf->uncertain = chf->closing = true;
    smf_chf_journal(chf, "invalid_final_usage", NULL, NULL, 0);
    return OGS_ERROR;
}

uint64_t c5_packet_units(smf_chf_sess_t *chf, uint32_t sequence)
{
    c5_owner_t *owner = find(chf);
    c5_count_t *count;
    if (!owner) return UINT64_MAX;
    count = &owner->reports[sequence % SMF_CHF_MAX_REPORTS];
    return count->present && count->sequence == sequence ? count->ul + count->dl : UINT64_MAX;
}

int c5_packet_urr(smf_sess_t *sess, smf_bearer_t *bearer)
{
    ogs_pfcp_urr_t *urr = bearer->urr;
    urr->meas_method = OGS_PFCP_MEASUREMENT_METHOD_VOLUME;
    memset(&urr->rep_triggers, 0, sizeof(urr->rep_triggers));
    memset(&urr->meas_info, 0, sizeof(urr->meas_info));
    memset(&urr->vol_quota, 0, sizeof(urr->vol_quota));
    memset(&urr->vol_threshold, 0, sizeof(urr->vol_threshold));
    urr->meas_info.mnop = 1;
    urr->meas_info.istm = 1; /* Start the native reporting timer at URR installation. */
    urr->meas_period = urr->time_threshold = urr->time_quota = 0;
    /* Report actual packet deltas each second using the supported validity
     * timer. Do not encode a packet grant as a volume quota. */
    urr->rep_triggers.quota_validity_time = 1;
    urr->quota_validity_time = 1;
    ogs_pfcp_pdr_associate_urr(bearer->ul_pdr, urr);
    ogs_pfcp_pdr_associate_urr(bearer->dl_pdr, urr);
    return OGS_OK;
}
