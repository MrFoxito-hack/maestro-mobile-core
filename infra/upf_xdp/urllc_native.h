/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Compile only into the dedicated URLLC UPF. No shared NF binary is replaced.
 * The native PFCP event loop and controller serialize on the same flock.
 */
#ifndef MAESTRO_URLLC_NATIVE_H
#define MAESTRO_URLLC_NATIVE_H
#include <linux/bpf.h>
#include <sys/syscall.h>
#include <sys/file.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <time.h>
#include <inttypes.h>

#define MX_DIR "/run/maestro-urllc-xdp"
#define MX_GATE "/run/maestro-bpf/urllc/maps/enabled"

static inline uint64_t mx_clock(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000ULL + t.tv_nsec;
}

static inline int mx_lock(void)
{
    int fd;
    if (!getenv("MAESTRO_URLLC_FASTPATH")) return -1;
    fd = open(MX_DIR "/control.lock", O_RDWR | O_CREAT | O_CLOEXEC, 0660);
    ogs_assert(fd >= 0);
    ogs_assert(flock(fd, LOCK_EX) == 0);
    return fd;
}

static inline void mx_unlock(int fd)
{
    if (fd >= 0) { flock(fd, LOCK_UN); close(fd); }
}

static inline void maestro_xdp_revoke(void)
{
    int lock = mx_lock(), fd;
    FILE *f;
    union bpf_attr a;
    uint32_t zero = 0;
    if (lock < 0) return;
    f = fopen(MX_DIR "/native.tmp", "w");
    ogs_assert(f);
    fprintf(f, "{\"eligible\":false,\"pid\":%d,\"generation\":\"%" PRIu64 "\"}\n", getpid(), mx_clock());
    ogs_assert(fclose(f) == 0);
    ogs_assert(rename(MX_DIR "/native.tmp", MX_DIR "/native.json") == 0);
    memset(&a, 0, sizeof(a));
    a.pathname = (uint64_t)(uintptr_t)MX_GATE;
    fd = syscall(__NR_bpf, BPF_OBJ_GET, &a, sizeof(a));
    if (fd < 0) {
        /* No pins means no forwarding. Every other failure stops rule mutation. */
        ogs_assert(errno == ENOENT);
    } else {
        memset(&a, 0, sizeof(a));
        a.map_fd = fd; a.key = (uint64_t)(uintptr_t)&zero;
        a.value = (uint64_t)(uintptr_t)&zero;
        ogs_assert(syscall(__NR_bpf, BPF_MAP_UPDATE_ELEM, &a, sizeof(a)) == 0);
        close(fd);
    }
    mx_unlock(lock);
}

static inline void maestro_xdp_publish(upf_sess_t *s)
{
    int lock = mx_lock(), n = 0, nq = 0, nu = 0, ns = 0, valid = 1;
    upf_sess_t *other;
    ogs_pfcp_pdr_t *p, *ul = NULL, *dl = NULL;
    ogs_pfcp_qer_t *q;
    ogs_pfcp_urr_t *u;
    char ue[INET_ADDRSTRLEN] = "", gnb[INET_ADDRSTRLEN] = "", upf[INET_ADDRSTRLEN] = "";
    uint64_t ul_mbr = 0, dl_mbr = 0;
    FILE *f;
    if (lock < 0) return;
    if (!s || !s->ipv4 || !s->apn_dnn || strcmp(s->apn_dnn, "5g-plus")) {
        mx_unlock(lock); return;
    }
    ogs_list_for_each(&upf_self()->sess_list, other) { ns++; }
    if (ns != 1) valid = 0;
    ogs_list_for_each(&s->pfcp.urr_list, u) { nu++; }
    ogs_list_for_each(&s->pfcp.qer_list, q) {
        nq++; ul_mbr += q->mbr.uplink; dl_mbr += q->mbr.downlink;
        if (q->gate_status.uplink || q->gate_status.downlink ||
            q->gbr.uplink || q->gbr.downlink || q->mbr.uplink || q->mbr.downlink) valid = 0;
    }
    ogs_list_for_each(&s->pfcp.pdr_list, p) {
        n++;
        if (!p->far || p->num_of_flow || p->num_of_urr || !ogs_list_empty(&p->rule_list) ||
            p->far->apply_action != OGS_PFCP_APPLY_ACTION_FORW) valid = 0;
        if (p->src_if == OGS_PFCP_INTERFACE_ACCESS) ul = p;
        else if (p->src_if == OGS_PFCP_INTERFACE_CORE) dl = p;
        else valid = 0;
    }
    if (n != 2 || nq != 1 || nu || !ul || !dl || !dl->far || !ul->far ||
        !ul->f_teid_len || !ul->f_teid.ipv4 || !dl->far->outer_header_creation_len ||
        !dl->qer || !dl->qer->qfi || upf_self()->charging_enforcement) valid = 0;
    if (ul && dl && ul->far && dl->far && dl->qer &&
        (ul->far->dst_if != OGS_PFCP_INTERFACE_CORE ||
         dl->far->dst_if != OGS_PFCP_INTERFACE_ACCESS ||
         !dl->far->outer_header_creation.gtpu4 || dl->far->outer_header_creation.gtpu6 ||
         ul->qer != dl->qer || (ul->qfi && ul->qfi != dl->qer->qfi))) valid = 0;
    if (ul && dl && dl->far) {
        inet_ntop(AF_INET, &s->ipv4->addr, ue, sizeof(ue));
        inet_ntop(AF_INET, &ul->f_teid.addr, upf, sizeof(upf));
        inet_ntop(AF_INET, &dl->far->outer_header_creation.addr, gnb, sizeof(gnb));
    }
    f = fopen(MX_DIR "/native.tmp", "w");
    ogs_assert(f);
    fprintf(f, "{\"eligible\":%s,\"pid\":%d,\"generation\":\"%" PRIu64 "\","
        "\"seid\":\"%" PRIu64 "\",\"ue\":\"%s\",\"gnb\":\"%s\",\"upf\":\"%s\","
        "\"ul_teid\":%u,\"dl_teid\":%u,\"qfi\":%u,\"pdr_count\":%d,\"urr_count\":%d,"
        "\"qer_count\":%d,\"ul_mbr\":%" PRIu64 ",\"dl_mbr\":%" PRIu64 ",\"dnn\":\"5g-plus\"}\n",
        valid ? "true" : "false", getpid(), mx_clock(), s->upf_n4_seid, ue, gnb, upf,
        ul ? ul->f_teid.teid : 0, dl && dl->far ? dl->far->outer_header_creation.teid : 0,
        dl && dl->qer ? dl->qer->qfi : 0, n, nu, nq, ul_mbr, dl_mbr);
    ogs_assert(fclose(f) == 0);
    ogs_assert(rename(MX_DIR "/native.tmp", MX_DIR "/native.json") == 0);
    mx_unlock(lock);
}
#endif
