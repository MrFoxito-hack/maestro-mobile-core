/* SPDX-License-Identifier: AGPL-3.0-or-later */
#include "native_deferred.h"
#include <stdlib.h>

struct mdq_entry {
    struct mdq_entry *next;
    uint64_t id, generation;
    void *event;
};

int mdq_push(mdq_queue *q, uint64_t id, uint64_t generation, void *event)
{
    mdq_entry *e = malloc(sizeof(*e));
    if (!e) return -1;
    *e = (mdq_entry){.id = id, .generation = generation, .event = event};
    if (q->tail) q->tail->next = e;
    else q->head = e;
    q->tail = e;
    q->count++;
    return 0;
}

void *mdq_take(mdq_queue *q, bool system_ready,
        mdq_valid_fn valid, mdq_dispose_fn dispose)
{
    if (!system_ready) return NULL;
    while (q->head) {
        mdq_entry *e = q->head;
        void *event = e->event;
        bool current = valid(e->id, e->generation, event);
        q->head = e->next;
        if (!q->head) q->tail = NULL;
        q->count--;
        free(e);
        if (current) return event;
        dispose(event);
    }
    return NULL;
}

void mdq_forget(mdq_queue *q, uint64_t id, mdq_dispose_fn dispose)
{
    mdq_entry **cursor = &q->head;
    q->tail = NULL;
    while (*cursor) {
        mdq_entry *e = *cursor;
        if (e->id == id) {
            *cursor = e->next;
            q->count--;
            dispose(e->event);
            free(e);
        } else {
            q->tail = e;
            cursor = &e->next;
        }
    }
}

void mdq_clear(mdq_queue *q, mdq_dispose_fn dispose)
{
    while (q->head) {
        mdq_entry *e = q->head;
        q->head = e->next;
        dispose(e->event);
        free(e);
    }
    *q = (mdq_queue){0};
}
