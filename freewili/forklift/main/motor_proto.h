/* Forklift serial protocol and L298N output mapping. Pure C, no Pico SDK, so it
 * is unit-tested on the laptop (freewili/forklift/tests/).
 *
 * Laptop -> Free-WiLi, one line each, newline-terminated (edge/fleet_edge/freewili_link.py):
 *   W,<left>,<right>   wheel speeds -100..100 (out-of-range values are clamped)
 *   F,<speed>,<ms>     fork motor at speed -100..100 (+ = up) for ms (capped at FORK_MAX_MS)
 *   S,<reason>         stop everything now
 * Free-WiLi -> laptop:
 *   T,<state>,<battery_v>   once per second
 *
 * Do not change these lines without changing freewili_link.py, docs/API.md section 3,
 * and telling the cloud lead. */
#ifndef FORKLIFT_MOTOR_PROTO_H
#define FORKLIFT_MOTOR_PROTO_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define FORK_MAX_MS 5000u   /* no end-stop switches: never run the fork longer than this */

typedef enum {
    CMD_NONE = 0,   /* blank, unknown or malformed: ignore it, do NOT feed the watchdog */
    CMD_WHEELS,
    CMD_FORK,
    CMD_STOP
} cmd_kind_t;

typedef struct {
    cmd_kind_t kind;
    int left;          /* CMD_WHEELS: -100..100 */
    int right;
    int fork;          /* CMD_FORK: -100..100 */
    uint32_t fork_ms;  /* CMD_FORK: 0..FORK_MAX_MS, 0 = stop the fork */
} cmd_t;

/* Parse one line (without its newline). Never reads past the terminator. */
cmd_t proto_parse_line(const char *line);

/* All three L298N channels run with their EN jumper ON, and the speed comes from
 * PWM on the IN pins. During the PWM off-time both INs are low, which the L298N
 * treats as a brake, so a zero command always holds the motor still. */

/* Drive channel: both IN pins are PWM-capable.
 *   speed > 0  -> IN_A = speed%,  IN_B = 0
 *   speed < 0  -> IN_A = 0,       IN_B = |speed|%
 *   speed == 0 -> both 0 (brake) */
typedef struct {
    uint8_t a_pct;
    uint8_t b_pct;
} pwm_pair_t;

pwm_pair_t drive_channel(int speed);

/* Fork channel: IN_A is PWM, IN_B is a plain on/off pin (its PWM output is
 * already used by a drive channel).
 *   speed > 0  -> IN_A = speed%,        IN_B = low   (up)
 *   speed < 0  -> IN_A = (100-|speed|)%, IN_B = high (down: drives while IN_A is low,
 *                                                     brakes while both are high)
 *   speed == 0 -> IN_A = 0, IN_B = low  (brake) */
typedef struct {
    uint8_t a_pct;
    bool b_high;
} fork_out_t;

fork_out_t fork_channel(int speed);

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
