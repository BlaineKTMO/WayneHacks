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

/* Split "X,<a>,<b>" after the prefix and parse both integers strictly. */
static bool parse_two_ints(const char *a, long *x, long *y) {
    const char *comma = strchr(a, ',');
    if (!comma) return false;
    const char *b = comma + 1;
    const char *end = b + strlen(b);
    while (end > b && (end[-1] == '\r' || end[-1] == ' ')) end--;
    return parse_int(a, comma, x) && parse_int(b, end, y);
}

cmd_t proto_parse_line(const char *line) {
    cmd_t c = {CMD_NONE, 0, 0, 0, 0};
    if (!line) return c;
    while (*line == ' ' || *line == '\r') line++;

    if (line[0] == 'S' && (line[1] == ',' || line[1] == '\0' || line[1] == '\r')) {
        c.kind = CMD_STOP;
        return c;
    }
    if (line[1] != ',') return c;

    long x, y;
    if (line[0] == 'W') {
        if (!parse_two_ints(line + 2, &x, &y)) return c;
        c.kind = CMD_WHEELS;
        c.left = clamp100(x);
        c.right = clamp100(y);
    } else if (line[0] == 'F') {
        if (!parse_two_ints(line + 2, &x, &y) || y < 0) return c;
        c.kind = CMD_FORK;
        c.fork = clamp100(x);
        c.fork_ms = y > (long)FORK_MAX_MS ? FORK_MAX_MS : (uint32_t)y;
    }
    return c;
}

pwm_pair_t drive_channel(int speed) {
    pwm_pair_t p = {0, 0};
    speed = clamp100(speed);
    if (speed > 0) p.a_pct = (uint8_t)speed;
    else if (speed < 0) p.b_pct = (uint8_t)(-speed);
    return p;
}

fork_out_t fork_channel(int speed) {
    fork_out_t f = {0, false};
    speed = clamp100(speed);
    if (speed > 0) {
        f.a_pct = (uint8_t)speed;
    } else if (speed < 0) {
        f.a_pct = (uint8_t)(100 + speed);   /* 100 - |speed| */
        f.b_high = true;
    }
    return f;
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
