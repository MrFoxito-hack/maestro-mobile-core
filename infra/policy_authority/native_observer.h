/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Native observation and opt-in local guard control, compiled into init.c.
 * Runs on the NF event thread. Coverage requires the complete build contract,
 * a live native fence, supported policy and no outstanding policy writers.
 */
#ifndef MAESTRO_POLICY_OBSERVER_H
#define MAESTRO_POLICY_OBSERVER_H
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <unistd.h>
#include <time.h>
#include <inttypes.h>
#include <errno.h>
#include "native_control.h"
#include "native_completion.h"
#include "native_coverage.h"
#if defined(MPO_SMF) || defined(MPO_PCF)
#include "native_sbi_deferred.h"
#endif
#if defined(MPO_SMF)
#include "native_smf_deferred.h"
#endif
#if defined(MPO_PCF)
#include "native_pcf.h"
#endif
#if defined(MPO_SMF) || defined(MPO_UPF)
#include "native_pfcp_ogs.h"
#endif

static int mpo_fd = -1;
static ogs_poll_t *mpo_poll;
static char mpo_boot[64];
static uint64_t mpo_generation;
static const char *mpo_path;

static unsigned mpo_pending_native(void)
{
    unsigned pending = 0;
#if defined(MPO_SMF) || defined(MPO_PCF)
    if (nsd_ready()) pending += nsd_count();
#endif
#if defined(MPO_PCF)
    pcf_ue_sm_t *ue;
    pcf_sess_t *session;
    ogs_list_for_each(&pcf_self()->pcf_ue_sm_list, ue)
        ogs_list_for_each(&ue->sess_list, session) {
            pending += ogs_list_count(&session->sbi.xact_list);
            pending += session->nwdaf_pending;
        }
#elif defined(MPO_SMF)
    smf_ue_t *ue;
    smf_sess_t *session;
    ogs_pfcp_node_t *node;
    ogs_pfcp_xact_t *xact;
    ogs_list_for_each(&smf_self()->smf_ue_list, ue)
        ogs_list_for_each(&ue->sess_list, session)
            pending += ogs_list_count(&session->sbi.xact_list);
    if (msd_ready()) pending += msd_count();
    ogs_list_for_each(&ogs_pfcp_self()->pfcp_peer_list, node) {
        /* Held restoration is not an in-flight writer. Counting it during an
         * expired lease would prevent the next system grant needed to drain. */
        pending += msd_ready() && node->restoration_required ? 1 : 0;
        ogs_list_for_each(&node->local_list, xact) {
            uint8_t type = xact->seq[0].type;
            if (type == 50 || type == 52 || type == 54) pending++;
        }
    }
#elif defined(MPO_UPF)
    ogs_pfcp_node_t *node;
    ogs_list_for_each(&ogs_pfcp_self()->pfcp_peer_list, node)
        pending += node->restoration_required ? 1 : 0;
#endif
    return pending;
}
#if defined(MPO_SMF)
#include "native_qos.h"
static ogs_timer_t *mpo_completion_timer;
static void mpo_completion_reply(uint32_t id, bool accepted)
{
    if (!accepted) mqa_cancel();
    ogs_sbi_stream_t *stream = ogs_sbi_stream_find_by_id(id);
    if (!stream) return;
    if (accepted) ogs_sbi_send_http_status_no_content(stream);
    else ogs_sbi_server_send_error(stream, OGS_SBI_HTTP_STATUS_SERVICE_UNAVAILABLE,
        NULL, "native_n4_completion_not_verified", NULL, NULL);
}
static void mpo_completion_tick(void *data)
{
    (void)data;
    mnc_poll();
    ogs_timer_start(mpo_completion_timer, ogs_time_from_msec(100));
}
#endif

static void mpo_string(FILE *f, const char *s)
{
    const unsigned char *p = (const unsigned char *)(s ? s : "");
    fputc('"', f);
    for (; *p; p++) {
        if (*p == '"' || *p == '\\') fprintf(f, "\\%c", *p);
        else if (*p < 32 || *p >= 127) fprintf(f, "\\u%04x", *p);
        else fputc(*p, f);
    }
    fputc('"', f);
}

static uint64_t mpo_now(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000ULL + t.tv_nsec;
}

#if defined(MPO_SMF) || defined(MPO_UPF)
static void mpo_ip4(FILE *f, uint32_t address)
{
    char ip[INET_ADDRSTRLEN];
    if (!inet_ntop(AF_INET, &address, ip, sizeof(ip))) ip[0] = 0;
    mpo_string(f, ip);
}

static void mpo_volume(FILE *f, ogs_pfcp_volume_threshold_t *v)
{
    fprintf(f, "{\"flags\":%u,\"total\":\"%" PRIu64 "\","
            "\"uplink\":\"%" PRIu64 "\",\"downlink\":\"%" PRIu64 "\"}",
            v->flags, v->total_volume, v->uplink_volume, v->downlink_volume);
}

static void mpo_hex(FILE *f, const void *data, size_t length)
{
    const unsigned char *p = data;
    size_t i;
    fputc('"', f);
    for (i = 0; i < length; i++) fprintf(f, "%02x", p[i]);
    fputc('"', f);
}

static void mpo_strings(FILE *f, char **items)
{
    int i;
    fputc('[', f);
    if (items) for (i = 0; items[i]; i++) {
        if (i) fputc(',', f);
        mpo_string(f, items[i]);
    }
    fputc(']', f);
}

static void mpo_filters(FILE *f, ogs_pfcp_pdr_t *pdr)
{
    ogs_pfcp_rule_t *rule;
    int n = 0;
    fputc('[', f);
    ogs_list_for_each(&pdr->rule_list, rule) {
        ogs_ipfw_rule_t *ip = &rule->ipfw;
        fprintf(f, "%s{\"flags\":%u,\"id\":%u,\"protocol\":%u,"
            "\"ipv4_src\":%u,\"ipv4_dst\":%u,\"ipv6_src\":%u,\"ipv6_dst\":%u,"
            "\"src_address\":", n++ ? "," : "", rule->flags, rule->sdf_filter_id,
            ip->proto, ip->ipv4_src, ip->ipv4_dst, ip->ipv6_src, ip->ipv6_dst);
        mpo_hex(f, ip->ip.src.addr, sizeof(ip->ip.src.addr));
        fputs(",\"src_mask\":", f); mpo_hex(f, ip->ip.src.mask, sizeof(ip->ip.src.mask));
        fputs(",\"dst_address\":", f); mpo_hex(f, ip->ip.dst.addr, sizeof(ip->ip.dst.addr));
        fputs(",\"dst_mask\":", f); mpo_hex(f, ip->ip.dst.mask, sizeof(ip->ip.dst.mask));
        fprintf(f, ",\"src_ports\":[%u,%u],\"dst_ports\":[%u,%u],"
            "\"tos_traffic_class\":%u,\"security_parameter_index\":%u,"
            "\"flow_label\":%u,\"sdf_filter_id\":%u}",
            ip->port.src.low, ip->port.src.high, ip->port.dst.low, ip->port.dst.high,
            ip->tos_traffic_class, ip->security_parameter_index, ip->flow_label, ip->sdf_filter_id);
    }
    fputc(']', f);
}

static void mpo_rules(FILE *f, ogs_pfcp_sess_t *sess)
{
    ogs_pfcp_pdr_t *p;
    ogs_pfcp_far_t *a;
    ogs_pfcp_qer_t *q;
    ogs_pfcp_urr_t *u;
    int n = 0, i;
    fprintf(f, "{\"pdr\":[");
    ogs_list_for_each(&sess->pdr_list, p) {
        fprintf(f, "%s{\"id\":%u,\"active\":true,\"precedence\":%u,"
                "\"source_interface\":%u,\"far_id\":", n++ ? "," : "",
                p->id, p->precedence, p->src_if);
        if (p->far) fprintf(f, "%u", p->far->id); else fputs("null", f);
        fputs(",\"qer_ids\":[", f);
        if (p->qer) fprintf(f, "%u", p->qer->id);
        fputs("],\"urr_ids\":[", f);
        for (i = 0; i < p->num_of_urr; i++) {
            if (i) fputc(',', f);
            if (p->urr[i]) fprintf(f, "%u", p->urr[i]->id); else fputs("null", f);
        }
        fprintf(f, "],\"qfi\":%u,\"f_teid_len\":%d,\"teid\":%u,\"teid_ipv4\":",
                p->qfi, p->f_teid_len, p->f_teid.teid);
        if (p->f_teid_len && p->f_teid.ipv4) mpo_ip4(f, p->f_teid.addr);
        else fputs("null", f);
        fputs(",\"dnn\":", f); mpo_string(f, p->dnn);
        fprintf(f, ",\"compiled_filter_count\":%d,\"flows\":[", ogs_list_count(&p->rule_list));
        for (i = 0; i < p->num_of_flow; i++) {
            fprintf(f, "%s{\"flags\":%u,\"id\":%u,\"description\":", i ? "," : "",
                    p->flow[i].flags, p->flow[i].sdf_filter_id);
            mpo_string(f, p->flow[i].description); fputc('}', f);
        }
        fprintf(f, "],\"source_interface_type_present\":%s,\"source_interface_type\":%u,"
            "\"outer_header_removal_len\":%d,\"outer_header_removal\":%u,"
            "\"gtpu_extheader_deletion\":%u,\"f_teid_flags\":%u,\"choose_id_present\":%s,"
            "\"choose_id\":%u,\"ue_ip_len\":%d,\"ue_ip_flags\":%u,\"ue_ipv4\":",
            p->src_if_type_presence ? "true" : "false", p->src_if_type,
            p->outer_header_removal_len, p->outer_header_removal.description,
            p->outer_header_removal.gtpu_extheader_deletion,
            *(const unsigned char *)&p->f_teid, p->chid ? "true" : "false", p->choose_id,
            p->ue_ip_addr_len, *(const unsigned char *)&p->ue_ip_addr);
        if (p->ue_ip_addr_len && p->ue_ip_addr.ipv4) mpo_ip4(f, p->ue_ip_addr.addr);
        else fputs("null", f);
        fputs(",\"ue_ipv6\":", f);
        if (p->ue_ip_addr_len && p->ue_ip_addr.ipv6)
            mpo_hex(f, p->ue_ip_addr.ipv4 ? p->ue_ip_addr.both.addr6 : p->ue_ip_addr.addr6, OGS_IPV6_LEN);
        else fputs("null", f);
        fputs(",\"teid_ipv6\":", f);
        if (p->f_teid_len && p->f_teid.ipv6)
            mpo_hex(f, p->f_teid.ipv4 ? p->f_teid.both.addr6 : p->f_teid.addr6, OGS_IPV6_LEN);
        else fputs("null", f);
        fputs(",\"ipv4_framed_routes\":", f); mpo_strings(f, p->ipv4_framed_routes);
        fputs(",\"ipv6_framed_routes\":", f); mpo_strings(f, p->ipv6_framed_routes);
        fputs(",\"compiled_filters\":", f); mpo_filters(f, p);
        fputc('}', f);
    }
    fputs("],\"far\":[", f); n = 0;
    ogs_list_for_each(&sess->far_list, a) {
        fprintf(f, "%s{\"id\":%u,\"active\":true,\"apply_action\":%u,"
                "\"destination_interface\":%u,\"outer_header_len\":%d,\"teid\":%u,"
                "\"peer_ipv4\":", n++ ? "," : "", a->id, a->apply_action,
                a->dst_if, a->outer_header_creation_len, a->outer_header_creation.teid);
        if (a->outer_header_creation.gtpu4 || a->outer_header_creation.udp4 || a->outer_header_creation.ip4)
            mpo_ip4(f, a->outer_header_creation.addr);
        else fputs("null", f);
        fputs(",\"dnn\":", f); mpo_string(f, a->dnn);
        fprintf(f, ",\"destination_interface_type_present\":%s,\"destination_interface_type\":%u,"
            "\"smreq_flags\":%u,\"bar_id\":%u,\"outer_header_flags\":",
            a->dst_if_type_presence ? "true" : "false", a->dst_if_type,
            a->smreq_flags.value, sess->bar ? sess->bar->id : 0);
        /* Only packed flag octets; never serialize padding or runtime pointers. */
        mpo_hex(f, &a->outer_header_creation, 2);
        fputs(",\"peer_ipv6\":", f);
        if (a->outer_header_creation.gtpu6 || a->outer_header_creation.udp6 || a->outer_header_creation.ip6)
            mpo_hex(f, (a->outer_header_creation.gtpu4 || a->outer_header_creation.udp4 || a->outer_header_creation.ip4)
                ? a->outer_header_creation.both.addr6 : a->outer_header_creation.addr6, OGS_IPV6_LEN);
        else fputs("null", f);
        fputc('}', f);
    }
    fputs("],\"qer\":[", f); n = 0;
    ogs_list_for_each(&sess->qer_list, q) {
        fprintf(f, "%s{\"id\":%u,\"active\":true,\"qfi\":%u,"
                "\"gate_ul\":%u,\"gate_dl\":%u,\"mbr_ul\":\"%" PRIu64 "\","
                "\"mbr_dl\":\"%" PRIu64 "\",\"gbr_ul\":\"%" PRIu64 "\","
                "\"gbr_dl\":\"%" PRIu64 "\"}", n++ ? "," : "", q->id, q->qfi,
                q->gate_status.uplink, q->gate_status.downlink, q->mbr.uplink,
                q->mbr.downlink, q->gbr.uplink, q->gbr.downlink);
    }
    fputs("],\"urr\":[", f); n = 0;
    ogs_list_for_each(&sess->urr_list, u) {
        fprintf(f, "%s{\"id\":%u,\"active\":true,\"measurement_method\":%u,"
                "\"reporting_triggers\":%u,\"volume_threshold\":", n++ ? "," : "",
                u->id, u->meas_method, (unsigned)u->rep_triggers.reptri_5 |
                ((unsigned)u->rep_triggers.reptri_6 << 8) |
                ((unsigned)u->rep_triggers.reptri_7 << 16));
        mpo_volume(f, &u->vol_threshold);
        fputs(",\"volume_quota\":", f); mpo_volume(f, &u->vol_quota);
        fprintf(f, ",\"time_threshold\":%u,\"time_quota\":%u,"
                "\"measurement_period\":%u,\"measurement_information\":%u,"
                "\"event_threshold\":%u,\"event_quota\":%u,\"quota_holding_time\":%u,"
                "\"quota_validity_time\":%u,\"dropped_dl_traffic_threshold\":{"
                "\"flags\":%u,\"packets\":\"%" PRIu64 "\",\"bytes\":\"%" PRIu64 "\"}}",
                u->time_threshold, u->time_quota, u->meas_period, u->meas_info.octet5,
                u->event_threshold, u->event_quota, u->quota_holding_time, u->quota_validity_time,
                u->dropped_dl_traffic_threshold.flags, u->dropped_dl_traffic_threshold.downlink_packets,
                u->dropped_dl_traffic_threshold.number_of_bytes_of_downlink_data);
    }
    fputs("]}", f);
}
#endif

static void mpo_sessions(FILE *f)
{
    int n = 0;
#ifdef MPO_PCF
    pcf_ue_sm_t *ue;
    pcf_sess_t *s;
    ogs_list_for_each(&pcf_self()->pcf_ue_sm_list, ue) {
        ogs_list_for_each(&ue->sess_list, s) {
            mpc_policy policy;
            fprintf(f, "%s{\"supi\":", n++ ? "," : ""); mpo_string(f, ue->supi);
            fprintf(f, ",\"pdu_id\":%u,\"context_id\":%u,\"dnn\":", s->psi, s->id);
            mpo_string(f, s->dnn);
            fputs(",\"policy_id\":", f); mpo_string(f, s->sm_policy_id);
            fputs(",\"notification_uri\":", f); mpo_string(f, s->notification_uri);
            fprintf(f, ",\"nwdaf_pending\":%s,\"nwdaf_throttled\":%s,\"policy\":",
                    s->nwdaf_pending ? "true" : "false", s->nwdaf_throttled ? "true" : "false");
            if (mpc_policy_get(s, &policy))
                fprintf(f, "{\"five_qi\":%u,\"mbr_ul\":\"%" PRIu64 "\",\"mbr_dl\":\"%" PRIu64 "\"}",
                    policy.five_qi, policy.uplink, policy.downlink);
            else fputs("null", f);
            fputc('}', f);
        }
    }
#elif defined(MPO_SMF)
    smf_ue_t *ue;
    smf_sess_t *s;
    ogs_list_for_each(&smf_self()->smf_ue_list, ue) {
        ogs_list_for_each(&ue->sess_list, s) {
            fprintf(f, "%s{\"supi\":", n++ ? "," : ""); mpo_string(f, ue->supi);
            fprintf(f, ",\"pdu_id\":%u,\"context_id\":%u,\"dnn\":", s->psi, s->id);
            mpo_string(f, s->session.name);
            fprintf(f, ",\"smf_seid\":\"%" PRIu64 "\",\"upf_seid\":\"%" PRIu64 "\","
                    "\"pending_modification\":%s,\"policy_id\":", s->smf_n4_seid,
                    s->upf_n4_seid, s->pending_modification_xact ? "true" : "false");
            mpo_string(f, s->policy_association.id);
            fprintf(f, ",\"policy\":{\"five_qi\":%u,\"mbr_ul\":\"%" PRIu64 "\",\"mbr_dl\":\"%" PRIu64 "\"}",
                s->session.qos.index, s->session.ambr.uplink, s->session.ambr.downlink);
            fputs(",\"rules\":", f); mpo_rules(f, &s->pfcp); fputc('}', f);
        }
    }
#elif defined(MPO_UPF)
    upf_sess_t *s;
    ogs_pfcp_urr_t *u;
    ogs_list_for_each(&upf_self()->sess_list, s) {
        int count = 0;
        fprintf(f, "%s{\"context_id\":%u,\"dnn\":", n++ ? "," : "", s->id);
        mpo_string(f, s->apn_dnn);
        fprintf(f, ",\"smf_seid\":\"%" PRIu64 "\",\"upf_seid\":\"%" PRIu64 "\",\"ue_ipv4\":",
                s->smf_n4_f_seid.seid, s->upf_n4_seid);
        if (s->ipv4) mpo_ip4(f, s->ipv4->addr[0]); else fputs("null", f);
        fputs(",\"smf_ipv4\":", f);
        if (s->smf_n4_f_seid.ip.ipv4) mpo_ip4(f, s->smf_n4_f_seid.ip.addr);
        else fputs("null", f);
        fputs(",\"rules\":", f); mpo_rules(f, &s->pfcp);
        /* Accounting is deliberately outside rules/policy and read-only. */
        fputs(",\"usage\":[", f);
        ogs_list_for_each(&s->pfcp.urr_list, u) {
            upf_sess_urr_acc_t *a;
            if (!u->id || u->id > OGS_MAX_NUM_OF_URR) continue;
            a = &s->urr_acc[u->id - 1];
            fprintf(f, "%s{\"urr_id\":%u,\"total_octets\":\"%" PRIu64 "\","
                    "\"ul_octets\":\"%" PRIu64 "\",\"dl_octets\":\"%" PRIu64 "\","
                    "\"total_packets\":\"%" PRIu64 "\",\"report_sequence\":%u}",
                    count++ ? "," : "", u->id, a->total_octets, a->ul_octets,
                    a->dl_octets, a->total_pkts, a->report_seqn);
        }
        fputs("]}", f);
    }
#endif
}

static bool mpo_profile_complete(void)
{
#if defined(MPO_PCF)
    pcf_ue_sm_t *ue;
    pcf_sess_t *s;
    mpc_policy policy;
    ogs_list_for_each(&pcf_self()->pcf_ue_sm_list, ue)
        ogs_list_for_each(&ue->sess_list, s)
            if (!mpc_policy_get(s, &policy)) return false;
#elif defined(MPO_SMF)
    smf_ue_t *ue;
    smf_sess_t *s;
    ogs_list_for_each(&smf_self()->smf_ue_list, ue)
        ogs_list_for_each(&ue->sess_list, s)
            if (s->epc || !s->smf_n4_seid || !s->upf_n4_seid ||
                    s->pending_modification_xact ||
                    ogs_list_count(&s->pfcp.qer_list) != 1 ||
                    !ogs_list_count(&s->pfcp.pdr_list)) return false;
#elif defined(MPO_UPF)
    upf_sess_t *s;
    ogs_list_for_each(&upf_self()->sess_list, s)
        if (!s->ipv4 || s->ipv6 || !s->smf_n4_f_seid.seid ||
                ogs_list_count(&s->pfcp.qer_list) != 1 ||
                !ogs_list_count(&s->pfcp.pdr_list)) return false;
#endif
    return true;
}

static void mpo_receive(short when, ogs_socket_t fd, void *data)
{
    struct sockaddr_un peer;
    socklen_t len = sizeof(peer);
    char request[512], response[512], *body = NULL;
    size_t length = 0;
    ssize_t count;
    FILE *f;
    unsigned pending, deferred = 0;
    bool complete;
    (void)when; (void)data;
    count = recvfrom(fd, request, sizeof(request), MSG_DONTWAIT, (struct sockaddr *)&peer, &len);
    if (count < 0) return;
    if (count != 8 || memcmp(request, "observe\n", 8)) {
        int size = mfc_control(request, (size_t)count, response, sizeof(response));
        if (size > 0 && (size_t)size < sizeof(response))
            sendto(fd, response, (size_t)size, MSG_DONTWAIT, (struct sockaddr *)&peer, len);
        return;
    }
    f = open_memstream(&body, &length);
    if (!f) return;
    pending = mpo_pending_native();
#if defined(MPO_SMF) || defined(MPO_PCF)
    deferred += nsd_count();
#endif
#if defined(MPO_SMF)
    deferred += msd_count();
#endif
    complete = mcv_complete(pending, deferred, mpo_profile_complete());
    fprintf(f, "{\"status\":\"success\",\"data\":{\"schema_version\":1,"
            "\"scope\":\"native_observation\",\"writer_fenced\":%s,"
            "\"policy_complete\":%s,\"coverage_profile\":\"ipv4-session-ambr-v1\","
            "\"coverage_build\":%d,\"deferred_sbi\":%u,\"boot_id\":",
            mcv_fenced() ? "true" : "false", complete ? "true" : "false",
            MAESTRO_NATIVE_COVERAGE_V1, deferred); mpo_string(f, mpo_boot);
    fprintf(f, ",\"fencing\":{\"enabled\":%s,\"failed\":%s,\"token\":\"%" PRIu64
            "\",\"version\":\"%" PRIu64 "\",\"prepared\":%s,\"recovery_required\":%s,"
            "\"pending_n7\":%s,\"pending_n4\":%u,\"system\":%s,\"system_admission\":%s,\"leased\":%s},"
            "\"pending_native\":%u,"
            "\"generation\":\"%" PRIu64 "\",\"measured_at_ns\":\"%" PRIu64 "\","
            "\"pid\":%d", mfc_enabled() ? "true" : "false",
            maestro_native_gate.failed ? "true" : "false", maestro_native_gate.token,
            maestro_native_gate.version, maestro_native_gate.prepared ? "true" : "false",
            maestro_native_gate.recovery_required ? "true" : "false",
            mnc_pending() ? "true" : "false", mnc_pending_n4(),
            maestro_native_gate.system ? "true" : "false", mfc_system_admit() ? "true" : "false",
            mf_leased(&maestro_native_gate) ? "true" : "false",
            pending,
            mpo_generation, mpo_now(), getpid());
#if defined(MPO_PCF)
    fprintf(f, ",\"mode\":\"%s\"", mpc_mode_autonomous() ? "AUTONOMOUS" : "MANUAL");
#endif
#if defined(MPO_SMF)
    fprintf(f, ",\"deferred_native\":%u", msd_count());
#endif
    fputs(",\"sessions\":[", f);
    mpo_sessions(f);
    fputs("]}}", f);
    if (fclose(f) == 0) {
        if (length <= 196608) sendto(fd, body, length, MSG_DONTWAIT, (struct sockaddr *)&peer, len);
        else {
            const char *error = "{\"status\":\"blocked\",\"error_code\":\"native_observation_too_large\"}";
            sendto(fd, error, strlen(error), MSG_DONTWAIT, (struct sockaddr *)&peer, len);
        }
    }
    free(body);
}

static void mpo_open(void)
{
    struct sockaddr_un addr = {0};
    struct stat st;
    FILE *boot;
    mpo_path = getenv("MAESTRO_POLICY_OBSERVER_SOCKET");
    if (!mpo_path) {
        /* Configuring durable fencing without its control endpoint is fatal. */
        ogs_assert(getenv("MAESTRO_POLICY_STATE_DIR") == NULL);
        return;
    }
    /* A dedicated RuntimeDirectory with mode 0700 is supplied by systemd. */
    ogs_assert(strlen(mpo_path) < sizeof(addr.sun_path));
    if (lstat(mpo_path, &st) == 0) {
        ogs_assert(S_ISSOCK(st.st_mode) && st.st_uid == geteuid());
        ogs_assert(unlink(mpo_path) == 0);
    } else ogs_assert(errno == ENOENT);
    boot = fopen("/proc/sys/kernel/random/boot_id", "r");
    ogs_assert(boot && fgets(mpo_boot, sizeof(mpo_boot), boot));
    fclose(boot); mpo_boot[strcspn(mpo_boot, "\r\n")] = 0;
    mpo_generation = mpo_now();
    ogs_assert(mfc_open(getenv("MAESTRO_POLICY_STATE_DIR"), mpo_now) == 0);
#if defined(MPO_SMF) || defined(MPO_UPF)
    if (mfc_enabled()) ogs_assert(mfp_enterprise() != 0);
#endif
#if defined(MPO_SMF)
    if (mfc_enabled()) {
        mnc_init(mpo_completion_reply);
        mpo_completion_timer = ogs_timer_add(ogs_app()->timer_mgr, mpo_completion_tick, NULL);
        ogs_assert(mpo_completion_timer);
        ogs_timer_start(mpo_completion_timer, ogs_time_from_msec(100));
    }
#endif
    addr.sun_family = AF_UNIX;
    memcpy(addr.sun_path, mpo_path, strlen(mpo_path) + 1);
    mpo_fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    ogs_assert(mpo_fd >= 0);
    ogs_assert(bind(mpo_fd, (struct sockaddr *)&addr, sizeof(addr)) == 0);
    ogs_assert(chmod(mpo_path, 0600) == 0);
    mpo_poll = ogs_pollset_add(ogs_app()->pollset, OGS_POLLIN, mpo_fd, mpo_receive, NULL);
    ogs_assert(mpo_poll);
}

static void mpo_close(void)
{
#if defined(MPO_SMF)
    if (mpo_completion_timer) ogs_timer_delete(mpo_completion_timer);
    mpo_completion_timer = NULL;
#endif
    if (mpo_poll) ogs_pollset_remove(mpo_poll);
    if (mpo_fd >= 0) { close(mpo_fd); unlink(mpo_path); }
    mfc_close();
    mpo_poll = NULL; mpo_fd = -1;
}
#endif
