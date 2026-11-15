/* Base station send queue and radio turn-taking for several forklifts on one channel.
 * Pure C: host-tested (tests/test_motor_proto.c).
 *
 * The laptop sends every forklift a W line 20 times a second. Each line is queued here
 * and sent as one packet. About every RF_POLL_MS per forklift, the packet goes out as a
 * POLL: that forklift may answer with telemetry, and nothing else is sent until its
 * answer arrives (rf_sched_heard) or RF_SLOT_MS passes. Between polls, packets go back to
 * back (~9 ms of air each).
 *
 * A newer W for a forklift replaces its older W still in the queue (only the latest
 * speed matters, and the queue can't build a backlog). S lines are never replaced or
 * dropped: if the queue is full, the oldest non-stop entry makes room. */
#ifndef FORKLIFT_RF_SCHED_H
#define FORKLIFT_RF_SCHED_H

#include <stdbool.h>
#include <stdint.h>

#include "rf_proto.h"

#define RF_QUEUE_LEN 8u
#define RF_POLL_MS 200u

typedef struct {
    uint8_t id;
    char text[RF_TEXT_MAX + 1];
} rf_out_t;

typedef struct {
    rf_out_t q[RF_QUEUE_LEN];
    unsigned count;
    uint8_t awaiting;                      /* id polled and not yet answered, 0 = none */
    uint32_t slot_until_ms;
    uint32_t last_poll_ms[RF_MAX_ID + 1];
    bool polled_ever[RF_MAX_ID + 1];
} rf_sched_t;

void rf_sched_init(rf_sched_t *s);

/* Queue `text` (no newline) for forklift `id`. False if it was dropped (bad id, too
 * long, or a full queue of stops). */
bool rf_sched_push(rf_sched_t *s, uint8_t id, const char *text);

/* The packet to send now, if any. *poll: send it as RF_KIND_POLL. */
bool rf_sched_next(rf_sched_t *s, uint32_t now_ms, rf_out_t *out, bool *poll);

/* Telemetry from `id` arrived: if we were waiting on it, the air is free again. */
void rf_sched_heard(rf_sched_t *s, uint8_t id);

#endif
