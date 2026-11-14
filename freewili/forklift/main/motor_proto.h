/* Forklift serial protocol and L298N wheel mapping. Pure C, no Pico SDK, so it
 * is unit-tested on the laptop (freewili/forklift/tests/).
 *
 * Laptop -> Free-WiLi, one line each, newline-terminated (edge/fleet_edge/freewili_link.py):
 *   W,<left>,<right>   wheel speeds -100..100 (out-of-range values are clamped)
 *   S,<reason>         stop now
 * Free-WiLi -> laptop:
 *   T,<state>,<battery_v>   once per second
 *
 * Do not change these lines without changing freewili_link.py and telling the cloud lead. */
#ifndef FORKLIFT_MOTOR_PROTO_H
#define FORKLIFT_MOTOR_PROTO_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum {
    CMD_NONE = 0,   /* blank, unknown or malformed: ignore it, do NOT feed the watchdog */
    CMD_WHEELS,
    CMD_STOP
} cmd_kind_t;

typedef struct {
    cmd_kind_t kind;
    int left;    /* -100..100, valid for CMD_WHEELS */
    int right;
} cmd_t;

/* Parse one line (without its newline). Never reads past the terminator. */
cmd_t proto_parse_line(const char *line);

/* One L298N channel: EN gets PWM, IN1/IN2 pick the direction.
 *   speed > 0  -> IN1=1 IN2=0, duty = speed%
 *   speed < 0  -> IN1=0 IN2=1, duty = |speed|%
 *   speed == 0 -> IN1=0 IN2=0, duty = 100%   ("fast motor stop" = brake, L298N datasheet)
 * Braking rather than coasting is deliberate: the forklift stops where it is told to. */
typedef struct {
    bool in1;
    bool in2;
    uint8_t duty_pct;   /* 0..100 */
} l298n_channel_t;

l298n_channel_t l298n_channel(int speed);

typedef enum {
    FL_IDLE = 0,
    FL_DRIVING,
    FL_BLOCKED,   /* reserved for Ring 4; the firmware never sets it */
    FL_STOPPED,
    FL_FAULT
} fl_state_t;

const char *fl_state_name(fl_state_t s);

/* "T,<state>,<battery_v>\n" into buf. Returns the length written (excluding NUL). */
size_t proto_format_telemetry(char *buf, size_t cap, fl_state_t s, float battery_v);

/* Line assembler: feed bytes from USB, get whole lines out. Over-long lines are
 * discarded whole (never truncated into a different, valid-looking command). */
#define PROTO_LINE_MAX 64u
typedef struct {
    char buf[PROTO_LINE_MAX];
    size_t len;
    bool overflow;
} line_rx_t;

void line_rx_init(line_rx_t *rx);
/* Returns true when `c` completed a line; the line is then in rx->buf (NUL-terminated). */
bool line_rx_feed(line_rx_t *rx, char c);

#endif
