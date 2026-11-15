#include "rf_sched.h"

#include <string.h>

static bool reached(uint32_t now, uint32_t t) { return (int32_t)(now - t) >= 0; }

void rf_sched_init(rf_sched_t *s) { memset(s, 0, sizeof *s); }

static void remove_at(rf_sched_t *s, unsigned i) {
    memmove(&s->q[i], &s->q[i + 1], (s->count - i - 1) * sizeof s->q[0]);
    s->count--;
}

bool rf_sched_push(rf_sched_t *s, uint8_t id, const char *text) {
    const size_t n = strlen(text);
    if (id < 1 || id > RF_MAX_ID || n == 0) return false;
    if (n > RF_TEXT_MAX && text[0] != 'S') return false;
    if (text[0] == 'W') {                       /* newest speed wins */
        for (unsigned i = 0; i < s->count; i++) {
            if (s->q[i].id == id && s->q[i].text[0] == 'W') { remove_at(s, i); break; }
        }
    }
    if (s->count == RF_QUEUE_LEN) {             /* make room: oldest non-stop goes */
        unsigned i = 0;
        while (i < s->count && s->q[i].text[0] == 'S') i++;
        if (i == s->count) return false;
        remove_at(s, i);
    }
    rf_out_t *e = &s->q[s->count++];
    e->id = id;
    const size_t k = n > RF_TEXT_MAX ? RF_TEXT_MAX : n;
    memcpy(e->text, text, k);
    e->text[k] = '\0';
    return true;
}

bool rf_sched_next(rf_sched_t *s, uint32_t now_ms, rf_out_t *out, bool *poll) {
    if (s->awaiting && !reached(now_ms, s->slot_until_ms)) return false;   /* reply window */
    s->awaiting = 0;
    if (s->count == 0) return false;
    *out = s->q[0];
    remove_at(s, 0);
    const uint8_t id = out->id;
    *poll = !s->polled_ever[id] || reached(now_ms, s->last_poll_ms[id] + RF_POLL_MS);
    if (*poll) {
        s->polled_ever[id] = true;
        s->last_poll_ms[id] = now_ms;
        s->awaiting = id;
        s->slot_until_ms = now_ms + RF_SLOT_MS;
    }
    return true;
}

void rf_sched_heard(rf_sched_t *s, uint8_t id) {
    if (s->awaiting == id) s->awaiting = 0;
}
