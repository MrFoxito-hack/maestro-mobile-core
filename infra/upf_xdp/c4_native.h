/* SPDX-License-Identifier: AGPL-3.0-or-later
 * C4 candidate publisher. Does not change PFCP policies or C3 observers.
 * No admission: the N4 importer and native shared-QER hooks must be verified
 * before this candidate can advertise forwarding readiness.
 */
#ifndef MAESTRO_C4_NATIVE_H
#define MAESTRO_C4_NATIVE_H
#include "c4_bridge.h"
void maestro_c4_publish(void);
void maestro_c4_revoke(void);
#ifdef MAESTRO_C4_IMPLEMENTATION
#include <inttypes.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <time.h>
#include <errno.h>
static uint64_t c4_instance;
static uint64_t c4_generation;

static void c4_registry(bool revoked)
{
    const char *directory = getenv("MAESTRO_C4_RUNTIME");
    char target[4096], temporary[4096];
    upf_sess_t *s;
    FILE *f;
    int fd, first = 1;
    if (!directory) return;
    /* Independent runtime directory; never overwrite the legacy/C3 state. */
    ogs_assert(directory[0] == '/');
    ogs_assert(strcmp(directory, "/run/maestro-urllc-xdp"));
    c4_instance = maestro_c4_instance();
    ogs_assert(c4_instance);
    ogs_assert(++c4_generation != 0);
    ogs_assert(snprintf(target, sizeof(target), "%s/registry-v1.json", directory) < sizeof(target));
    ogs_assert(snprintf(temporary, sizeof(temporary), "%s/registry-v1.tmp", directory) < sizeof(temporary));
    fd = open(temporary, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW | O_CLOEXEC, 0600);
    ogs_assert(fd >= 0);
    f = fdopen(fd, "w");
    ogs_assert(f);
    fprintf(f, "{\"schema\":1,\"instance\":\"%" PRIu64 "\",\"generation\":\"%" PRIu64
        "\",\"pid\":%d,\"revoked\":%s,\"ready\":false,"
        "\"capabilities\":{\"shared_qer\":true,\"urr_import\":true,\"restart_replay\":false},\"sessions\":[",
        c4_instance, c4_generation, getpid(), revoked ? "true" : "false");
    if (!revoked) ogs_list_for_each(&upf_self()->sess_list, s) {
        ogs_pfcp_pdr_t *p;
        ogs_pfcp_qer_t *q;
        ogs_pfcp_urr_t *u;
        char ue[INET_ADDRSTRLEN];
        int comma = 0;
        const char *dnn = s->apn_dnn;
        if (!dnn) {
            ogs_pfcp_pdr_t *p_check;
            ogs_list_for_each(&s->pfcp.pdr_list, p_check) if (p_check->dnn) { dnn = p_check->dnn; break; }
        }
        if (!s->ipv4 || !dnn || strcmp(dnn, "5g-plus")) continue;
        inet_ntop(AF_INET, &s->ipv4->addr, ue, sizeof(ue));
        fprintf(f, "%s{", first ? "" : ",");
        maestro_c4_metadata(f,s);
        fprintf(f, "\"seid\":\"%" PRIu64 "\",\"ue\":\"%s\",\"dnn\":\"5g-plus\","
            "\"identity_verified\":false,\"pdrs\":[", s->upf_n4_seid, ue);
        first = 0;
        ogs_list_for_each(&s->pfcp.pdr_list, p) {
            int i;
            char upf[INET_ADDRSTRLEN] = "", gnb[INET_ADDRSTRLEN] = "";
            if (p->f_teid.ipv4) inet_ntop(AF_INET, &p->f_teid.addr, upf, sizeof(upf));
            if (p->far) inet_ntop(AF_INET, &p->far->outer_header_creation.addr, gnb, sizeof(gnb));
            fprintf(f, "%s{\"id\":%u,\"source\":%u,\"qer_id\":%u,\"qfi\":%u,"
                "\"teid\":%u,\"sdf\":%s,\"far_action\":%u,"
                "\"upf\":\"%s\",\"gnb\":\"%s\",\"dl_teid\":%u,\"urr_ids\":[",
                comma ? "," : "", p->id, p->src_if, p->qer ? p->qer->id : 0,
                p->qfi, p->f_teid.teid,
                p->num_of_flow || !ogs_list_empty(&p->rule_list) ? "true" : "false",
                p->far ? p->far->apply_action : 0, upf, gnb,
                p->far ? p->far->outer_header_creation.teid : 0);
            for (i = 0; i < p->num_of_urr; i++)
                fprintf(f, "%s%u", i ? "," : "", p->urr[i]->id);
            fputs("]}", f); comma = 1;
        }
        fputs("],\"qers\":[", f); comma = 0;
        ogs_list_for_each(&s->pfcp.qer_list, q) {
            fprintf(f, "%s{\"id\":%u,\"qfi\":%u,\"mbr\":[%" PRIu64 ",%" PRIu64
                "],\"gbr\":[%" PRIu64 ",%" PRIu64 "],\"gates\":[%u,%u]}",
                comma ? "," : "", q->id, q->qfi, q->mbr.uplink, q->mbr.downlink,
                q->gbr.uplink, q->gbr.downlink, q->gate_status.uplink, q->gate_status.downlink);
            comma = 1;
        }
        fputs("],\"urrs\":[", f); comma = 0;
        ogs_list_for_each(&s->pfcp.urr_list, u) {
            upf_sess_urr_acc_t *a;
            ogs_assert(u->id > 0 && u->id <= OGS_MAX_NUM_OF_URR);
            a = &s->urr_acc[u->id - 1];
            fprintf(f, "%s{\"id\":%u,\"quota_active\":%s,\"quota_limit\":%" PRIu64
                ",\"quota_expired\":%s,\"native_bytes\":%" PRIu64 ",\"native_packets\":%" PRIu64 "}",
                comma ? "," : "", u->id, a->quota.active ? "true" : "false", a->quota.limit,
                a->quota.expired ? "true" : "false", a->total_octets, a->total_pkts);
            comma = 1;
        }
        fputs("]}", f);
    }
    fputs("]}\n", f);
    ogs_assert(fflush(f) == 0);
    ogs_assert(fsync(fd) == 0);
    ogs_assert(fclose(f) == 0);
    ogs_assert(rename(temporary, target) == 0);
}
void maestro_c4_publish(void) { c4_registry(false); }
void maestro_c4_revoke(void) { c4_registry(true); }
#endif
#endif
