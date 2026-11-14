#include "motor_proto.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int clamp100(long v) {
    if (v > 100) return 100;
    if (v < -100) return -100;
    return (int)v;
}

/* Strict integer parse of [s, end): optional sign, at least one digit, nothing else. */
static bool parse_int(const char *s, const char *end, long *out) {
    if (s >= end) return false;
    char tmp[16];
    size_t n = (size_t)(end - s);
    if (n >= sizeof tmp) return false;
    memcpy(tmp, s, n);
    tmp[n] = '\0';
    char *e = NULL;
    long v = strtol(tmp, &e, 10);
    if (e == tmp || *e != '\0') return false;
    *out = v;
    return true;
}

cmd_t proto_parse_line(const char *line) {
    cmd_t c = {CMD_NONE, 0, 0};
    if (!line) return c;
    while (*line == ' ' || *line == '\r') line++;

    if (line[0] == 'S' && (line[1] == ',' || line[1] == '\0' || line[1] == '\r')) {
        c.kind = CMD_STOP;
        return c;
    }
    if (line[0] != 'W' || line[1] != ',') return c;

    const char *a = line + 2;
    const char *comma = strchr(a, ',');
    if (!comma) return c;
    const char *b = comma + 1;
    const char *end = b + strlen(b);
    while (end > b && (end[-1] == '\r' || end[-1] == ' ')) end--;

    long l, r;
    if (!parse_int(a, comma, &l) || !parse_int(b, end, &r)) return c;
    c.kind = CMD_WHEELS;
    c.left = clamp100(l);
    c.right = clamp100(r);
    return c;
}

l298n_channel_t l298n_channel(int speed) {
    l298n_channel_t ch;
    speed = clamp100(speed);
    if (speed > 0) {
        ch.in1 = true;  ch.in2 = false; ch.duty_pct = (uint8_t)speed;
    } else if (speed < 0) {
        ch.in1 = false; ch.in2 = true;  ch.duty_pct = (uint8_t)(-speed);
    } else {
        ch.in1 = false; ch.in2 = false; ch.duty_pct = 100;   /* brake */
    }
    return ch;
}

const char *fl_state_name(fl_state_t s) {
    switch (s) {
    case FL_IDLE:    return "IDLE";
    case FL_DRIVING: return "DRIVING";
    case FL_BLOCKED: return "BLOCKED";
    case FL_STOPPED: return "STOPPED";
    case FL_FAULT:   return "FAULT";
    }
    return "FAULT";
}

size_t proto_format_telemetry(char *buf, size_t cap, fl_state_t s, float battery_v) {
    int n = snprintf(buf, cap, "T,%s,%.2f\n", fl_state_name(s), (double)battery_v);
    if (n < 0) return 0;
    return (size_t)n < cap ? (size_t)n : cap - 1;
}

void line_rx_init(line_rx_t *rx) {
    rx->len = 0;
    rx->overflow = false;
    rx->buf[0] = '\0';
}

bool line_rx_feed(line_rx_t *rx, char c) {
    if (c == '\n' || c == '\r') {
        bool complete = rx->len > 0 && !rx->overflow;
        rx->buf[complete ? rx->len : 0] = '\0';
        rx->len = 0;
        rx->overflow = false;
        return complete;
    }
    if (rx->len < PROTO_LINE_MAX - 1) {
        rx->buf[rx->len++] = c;
    } else {
        rx->overflow = true;
    }
    return false;
}
