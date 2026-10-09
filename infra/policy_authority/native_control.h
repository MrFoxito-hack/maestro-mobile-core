/* SPDX-License-Identifier: AGPL-3.0-or-later */
#ifndef MAESTRO_NATIVE_CONTROL_H
#define MAESTRO_NATIVE_CONTROL_H
#include "native_fence.h"
#define MFC_HEX_BYTES (MF_ENVELOPE_BYTES * 2 + 1)
extern mf_gate maestro_native_gate;
int mfc_open(const char *state_dir, mf_clock_fn clock);
void mfc_close(void);
bool mfc_enabled(void);
bool mfc_system_admit(void);
bool mfc_current(uint64_t target);
int mfc_enter(const char *hex, uint64_t target);
void mfc_leave(void);
int mfc_export(uint64_t target, char out[MFC_HEX_BYTES]);
/* Local transport only, inside the NF-owned 0700 runtime directory. Never
 * expose this parser on an IP socket. The Core adapter owns external JSON v1.
 */
int mfc_control(const char *request, size_t length, char *reply, size_t capacity);
#endif
