/* SPDX-License-Identifier: AGPL-3.0-or-later */
#ifndef MAESTRO_NATIVE_DEFERRED_H
#define MAESTRO_NATIVE_DEFERRED_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct mdq_entry mdq_entry;
typedef struct mdq_queue {
    mdq_entry *head, *tail;
    size_t count;
} mdq_queue;
typedef bool (*mdq_valid_fn)(uint64_t id, uint64_t generation, void *event);
typedef void (*mdq_dispose_fn)(void *event);

/* NF event thread only. A successful push transfers ownership. Allocation
 * failure leaves ownership with the caller, which must fail closed explicitly.
 * No accounting payload is serialized, copied, or rolled back by this queue. */
int mdq_push(mdq_queue *q, uint64_t id, uint64_t generation, void *event);
void *mdq_take(mdq_queue *q, bool system_ready,
        mdq_valid_fn valid, mdq_dispose_fn dispose);
void mdq_forget(mdq_queue *q, uint64_t id, mdq_dispose_fn dispose);
void mdq_clear(mdq_queue *q, mdq_dispose_fn dispose);
#endif
