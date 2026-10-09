/* SPDX-License-Identifier: AGPL-3.0-or-later
 * Event-thread fencing primitives. No Open5GS structs or charging state here.
 * All callers must run on the same NF event thread. This file alone does not
 * establish all-writer coverage; each effective mutation boundary must call it.
 */
#ifndef MAESTRO_NATIVE_FENCE_H
#define MAESTRO_NATIVE_FENCE_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MF_COOKIE_BYTES 32
#define MF_ENVELOPE_BYTES 57
#define MF_MAX_LEASE_NS UINT64_C(60000000000)

typedef uint64_t (*mf_clock_fn)(void);
typedef struct mf_gate {
    uint64_t token, version, deadline_ns, serial, target_seid;
    unsigned char cookie[MF_COOKIE_BYTES];
    char core_boot[37];
    char state_dir[512];
    bool prepared, failed, recovery_required, system, admission_closed;
    int lock_fd;
    mf_clock_fn clock;
} mf_gate;

/* Returns 0 on success. Any persistence uncertainty makes the gate fail closed. */
int mf_open(mf_gate *g, const char *state_dir, mf_clock_fn clock);
void mf_close(mf_gate *g);
int mf_fence(mf_gate *g, uint64_t token, const char *core_boot, uint64_t remaining_ns);
int mf_prepare(mf_gate *g, uint64_t token, uint64_t version, uint64_t seid,
               const unsigned char cookie[MF_COOKIE_BYTES]);
int mf_prepare_system(mf_gate *g, uint64_t token, uint64_t version,
                      const unsigned char cookie[MF_COOKIE_BYTES]);
bool mf_system_ready(const mf_gate *g);
bool mf_system_admit(const mf_gate *g);
int mf_system_admission(mf_gate *g, uint64_t token, bool open);
int mf_finish(mf_gate *g, uint64_t token, uint64_t version);
void mf_cancel(mf_gate *g);
bool mf_leased(const mf_gate *g);
bool mf_authorized(const mf_gate *g, uint64_t token, uint64_t version,
                   uint64_t seid, const unsigned char cookie[MF_COOKIE_BYTES]);
/* Wire format is an internal, versioned envelope; no PFCP IE number is implied.
 * The transport integration must authenticate this before touching NF state.
 */
int mf_encode(const mf_gate *g, uint64_t seid, unsigned char out[MF_ENVELOPE_BYTES]);
int mf_decode(const mf_gate *g, uint64_t seid, const unsigned char *data, size_t size,
              uint64_t *token, uint64_t *serial);
/* Revalidate after queueing: a fence/expiry between receive and dispatch wins. */
bool mf_stamp_valid(const mf_gate *g, uint64_t token, uint64_t serial, uint64_t seid);
#endif
