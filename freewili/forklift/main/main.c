/* Forklift motor controller: Free-WiLi OG main CPU.
 *
 * The edge laptop sends W/F/S lines over USB serial; this drives three L298Ns from
 * the breakout header and reports T lines once a second. Protocol: motor_proto.h.
 *
 * Four-wheel skid steer + fork, three L298Ns, EN jumpers ON on all of them.
 * Speed is PWM on the IN pins. Drivers 1 (front axle) and 2 (rear axle) are wired
 * in parallel: each header pin goes to the same IN pin on both boards.
 *
 *   header  GPIO  PWM   goes to
 *   9        8    4A    IN1 on drivers 1+2   left side
 *   11      11    5B    IN2 on drivers 1+2   left side
 *   15      14    7A    IN3 on drivers 1+2   right side   (FPGA SPI group)
 *   13      15    7B    IN4 on drivers 1+2   right side   (FPGA SPI group)
 *   1       13    6B    IN1 on driver 3      fork         (FPGA SPI group)
 *   3       27    --    IN2 on driver 3      fork, on/off only: its PWM output is 5B,
 *                                            which GPIO 11 already uses
 *
 * All six are outputs in the board's DEFAULT direction config, so this app never
 * changes a direction (that BSP path has not been verified on hardware). The SPI-group
 * pins are claimed only after IO_CONFIG is confirmed closed. Until then they sit at
 * the RP2040's reset pull-down, i.e. low = brake.
 *
 * Header pin 4 (V PINS IN) must be powered (jumper hdr 6 -> hdr 4) or no pin moves.
 *
 * Commands arrive over USB serial, over WiFi (a Bottlenose ESP32-C6 on the Qwiic /
 * breakout I2C pins hdr 10 SDA / hdr 8 SCL relays the same text lines, wifi_link.h), or
 * over RADIO from a base-station OG on the laptop (CC1101, 915 MHz, rf_link.h).
 * All three feed the same parser and the same 500 ms watchdog; telemetry goes to all.
 */
#include "fwog_main.h"
#include "hardware/clocks.h"
#include "hardware/pwm.h"
#include "hardware/uart.h"
#include "pico/stdlib.h"

#include <stdio.h>

#include "motor_proto.h"
#include "rf_link.h"
#include "rf_proto.h"
#include "wifi_link.h"

#include <string.h>

/* Kicks the BSP's 8.3 s board watchdog from the loop below. The 500 ms motor
 * watchdog (CMD_TIMEOUT_MS) is separate and much stricter. */
FWOG_WATCHDOG_DEFAULT();

#define PIN_LEFT_A  8u    /* hdr 9  */
/* Not hdr 11 (GPIO 11): on two boards it never followed the firmware (stuck at 0 V on
 * one, 3.3 V on the other, with nothing attached). Hdr 17 is GPIO 25, which is also the
 * main CPU's LED, so the LED lights while the left side reverses. PWM slice 4B; GPIO 8
 * (LEFT_A) is 4A, same frequency, so sharing the slice is fine. */
#define PIN_LEFT_B  25u   /* hdr 17 */
#define PIN_RIGHT_A 14u   /* hdr 15 */
#define PIN_RIGHT_B 15u   /* hdr 13 */
#define PIN_FORK_A  13u   /* hdr 1  */
#define PIN_FORK_B  27u   /* hdr 3  */

#define PWM_HZ 10000u
#define PWM_WRAP 999u
#define CMD_TIMEOUT_MS 500u    /* no valid W/F/S line for this long -> everything stops */
#define TELEMETRY_MS 1000u
#define CONFIG_RETRY_MS 1000u

/* main -> display, payload [MSG_FORKLIFT_STATUS, fl_state_t]. 0x01-0x1F belong to the
 * bootloader and 0x20-0x22 to the breakout I/O protocol (bsp/common/link/). */
#define MSG_FORKLIFT_STATUS 0x40u
/* main -> display, payload [MSG_FORKLIFT_IP, ascii...]: the WiFi module's IP address. */
#define MSG_FORKLIFT_IP 0x41u
#define IP_TEXT_MAX 20u

/* The OG cannot measure the motor battery (the breakout lines are digital). */
#define BATTERY_V_UNKNOWN 0.0f

static fl_state_t s_state = FL_FAULT;
static bool s_io_ready;        /* IO_CONFIG known-closed and all six pins claimed */
static bool s_wheels_moving;
static bool s_fork_moving;
static absolute_time_t s_fork_until;
static char s_ip[IP_TEXT_MAX + 1];   /* last "I,<ip>" from the WiFi module, "" if none */
#define RF_RETRY_MS 2000u
#define RF_QUIET_MS 200u     /* no radio command this long: base is idle-listening */
#define RF_REPLY_GAP_MS 5u   /* after a command, before replying: the base is back in RX */
static bool s_rf_telem_due;          /* telemetry waiting for a free slot on the air */
static uint8_t s_rf_seq;

static void pwm_pin_init(unsigned gpio) {
    const unsigned slice = pwm_gpio_to_slice_num(gpio);
    pwm_config cfg = pwm_get_default_config();
    pwm_config_set_clkdiv(&cfg, (float)clock_get_hz(clk_sys) / (float)(PWM_HZ * (PWM_WRAP + 1u)));
    pwm_config_set_wrap(&cfg, PWM_WRAP);
    pwm_init(slice, &cfg, false);
    pwm_set_gpio_level(gpio, 0);       /* level BEFORE the pad switches to PWM: no glitch */
    pwm_set_enabled(slice, true);
    gpio_set_function(gpio, GPIO_FUNC_PWM);
}

static void set_pct(unsigned gpio, uint8_t pct) {
    pwm_set_gpio_level(gpio, (uint16_t)((uint32_t)pct * (PWM_WRAP + 1u) / 100u));
}

static void wheels_brake(void) {
    set_pct(PIN_LEFT_A, 0);
    set_pct(PIN_LEFT_B, 0);
    if (s_io_ready) {
        set_pct(PIN_RIGHT_A, 0);
        set_pct(PIN_RIGHT_B, 0);
    }
    s_wheels_moving = false;
}

static void fork_brake(void) {
    if (s_io_ready) {
        set_pct(PIN_FORK_A, 0);
        (void)fwog_io_pin_drive(PIN_FORK_B, false);
    }
    s_fork_moving = false;
}

static void all_brake(void) {
    wheels_brake();
    fork_brake();
}

static void apply_wheels(int left, int right) {
    const pwm_pair_t l = drive_channel(left);
    const pwm_pair_t r = drive_channel(right);
    set_pct(PIN_LEFT_A, l.a_pct);
    set_pct(PIN_LEFT_B, l.b_pct);
    set_pct(PIN_RIGHT_A, r.a_pct);
    set_pct(PIN_RIGHT_B, r.b_pct);
    s_wheels_moving = (left != 0 || right != 0);
}

/* Returns false if the BSP refused the fork's on/off pin. */
static bool apply_fork(int speed, uint32_t ms) {
    if (speed == 0 || ms == 0) {
        fork_brake();
        return true;
    }
    const fork_out_t f = fork_channel(speed);
    set_pct(PIN_FORK_A, 0);             /* never pass through a full-drive state */
    if (fwog_io_pin_drive(PIN_FORK_B, f.b_high) != FWOG_IO_PIN_OK) {
        fork_brake();
        return false;
    }
    set_pct(PIN_FORK_A, f.a_pct);
    s_fork_moving = true;
    s_fork_until = make_timeout_time_ms(ms);
    return true;
}

static void send_status_to_display(fl_state_t s) {
    /* Best effort, and NEVER blocking: fwog_link_uart_send_frame() is a blocking
       write on a flow-controlled UART, so if the display stopped reading, this CPU
       would hang inside it and the 500 ms motor watchdog would never run. A 7-byte
       frame always fits the 32-byte TX FIFO, so send only when the FIFO is empty. */
    if (!(uart_get_hw(uart0)->fr & UART_UARTFR_TXFE_BITS)) return;
    const uint8_t payload[2] = {MSG_FORKLIFT_STATUS, (uint8_t)s};
    (void)fwog_link_uart_send_frame(payload, sizeof payload);
}

static void send_ip_to_display(void) {
    /* Same never-block rule as the status frame (<= 23 bytes fits the 32-byte FIFO). */
    if (!s_ip[0] || !(uart_get_hw(uart0)->fr & UART_UARTFR_TXFE_BITS)) return;
    uint8_t payload[1 + IP_TEXT_MAX];
    const size_t n = strlen(s_ip);
    payload[0] = MSG_FORKLIFT_IP;
    memcpy(payload + 1, s_ip, n);
    (void)fwog_link_uart_send_frame(payload, 1 + n);
}

static void set_state(fl_state_t s) {
    if (s == s_state) return;
    s_state = s;
    send_status_to_display(s);
}

static void update_moving_state(void) {
    set_state((s_wheels_moving || s_fork_moving) ? FL_DRIVING : FL_IDLE);
}

static void send_telemetry(void) {
    char buf[32];
    const size_t n = proto_format_telemetry(buf, sizeof buf, s_state, BATTERY_V_UNKNOWN);
    fwrite(buf, 1, n, stdout);
    fflush(stdout);
    wifi_link_send(buf, n);
    s_rf_telem_due = true;   /* sent from the main loop in the next free radio slot */
}

/* Telemetry back to the base. Sent right after a command arrives (the base switches to
   receive the moment it finishes sending, and the next command is ~50 ms away), or on
   its own when commands have stopped, so the two ends never transmit over each other. */
static void rf_send_telemetry(void) {
    char line[32];
    size_t n = proto_format_telemetry(line, sizeof line, s_state, BATTERY_V_UNKNOWN);
    if (n && line[n - 1] == '\n') line[n - 1] = '\0';
    uint8_t pkt[RF_PAYLOAD_LEN];
    if (rf_pack(pkt, RF_KIND_TELEM, RF_FORKLIFT_ID, s_rf_seq++, line))
        (void)rf_link_send(pkt, RF_PAYLOAD_LEN);
    s_rf_telem_due = false;
}

/* Close the FPGA's IO_CONFIG window, then claim the SPI-group pins and the fork's
 * on/off pin. Needs the display app to be servicing the link (display/main.c). */
static void try_io_ready(void) {
    if (!(fwog_io_config_window(false) &&
          fwog_io_config_state() == FWOG_IO_CONFIG_DISABLED)) {
        DIAG("[forklift] IO_CONFIG close not acked by display; retrying\n");
        set_state(FL_FAULT);
        return;
    }
    if (fwog_io_pin_drive(PIN_FORK_B, false) != FWOG_IO_PIN_OK) {
        DIAG("[forklift] fork pin (hdr 3) refused; retrying\n");
        set_state(FL_FAULT);
        return;
    }
    pwm_pin_init(PIN_RIGHT_A);
    pwm_pin_init(PIN_RIGHT_B);
    pwm_pin_init(PIN_FORK_A);
    s_io_ready = true;
    DIAG("[forklift] breakout ready\n");
    set_state(FL_IDLE);
}

static void handle_line(const char *line, absolute_time_t *last_cmd) {
    if (line[0] == '?' && (line[1] == '0' || line[1] == '1') && line[2] == '\0') {
        rf_link_select(line[1] - '0');           /* diagnostics: pick radio CS0/CS1 */
        return;
    }
    if (line[0] == '?' && line[1] == '\0') {   /* radio diagnostics (USB only) */
        char r[200];
        rf_link_report(r, sizeof r);
        fputs(r, stdout);
        fflush(stdout);
        return;
    }
    if (line[0] == 'I' && line[1] == ',') {    /* WiFi module's IP: show it, not a command */
        strncpy(s_ip, line + 2, IP_TEXT_MAX);
        s_ip[IP_TEXT_MAX] = '\0';
        send_ip_to_display();
        return;
    }
    const cmd_t c = proto_parse_line(line);
    if (c.kind == CMD_NONE) return;            /* garbage never feeds the watchdog */
    *last_cmd = get_absolute_time();

    switch (c.kind) {
    case CMD_STOP:
        all_brake();
        if (s_io_ready) set_state(FL_STOPPED);
        return;
    case CMD_WHEELS:
        if (!s_io_ready) return;               /* FAULT: stay braked */
        apply_wheels(c.left, c.right);
        break;
    case CMD_FORK:
        if (!s_io_ready) return;
        if (!apply_fork(c.fork, c.fork_ms)) {
            DIAG("[forklift] fork pin refused; braking everything\n");
            all_brake();
            set_state(FL_FAULT);
            return;
        }
        break;
    case CMD_NONE:
        return;
    }
    update_moving_state();
}

int main(void) {
    board_init();

    /* Left side braked immediately: GPIO 8/25 are not in the FPGA SPI group, so they
       can be claimed before the display is up. The rest wait for try_io_ready(). */
    pwm_pin_init(PIN_LEFT_A);
    pwm_pin_init(PIN_LEFT_B);

    /* Breakout I2C has no direction bit and isn't in the FPGA SPI group: safe now. */
    wifi_link_init();

    /* Brings the display CPU up (and reflashes it if its image changed). Performs
       board_release_display() itself -- do not call it separately. */
    const fwog_display_result_t d = fwog_display_update_run();
    DIAG("[forklift] display: %s\n", fwog_display_result_text(d));

    try_io_ready();

    line_rx_t rx, wifi_rx;     /* separate buffers: USB and WiFi bytes never interleave */
    line_rx_init(&rx);
    line_rx_init(&wifi_rx);
    absolute_time_t last_cmd = get_absolute_time();
    absolute_time_t next_telemetry = make_timeout_time_ms(TELEMETRY_MS);
    absolute_time_t next_retry = make_timeout_time_ms(CONFIG_RETRY_MS);
    absolute_time_t next_rf_retry = get_absolute_time();
    absolute_time_t last_rf_cmd = nil_time;

    while (true) {
        board_watchdog_kick();

        int ch;
        while ((ch = getchar_timeout_us(0)) != PICO_ERROR_TIMEOUT) {
            if (line_rx_feed(&rx, (char)ch)) handle_line(rx.buf, &last_cmd);
        }
        char wc;
        while (wifi_link_getc(&wc)) {
            if (line_rx_feed(&wifi_rx, wc)) handle_line(wifi_rx.buf, &last_cmd);
        }

        /* Radio: only once the display has answered (s_io_ready), which means it has
           already switched the antenna paths to 900 MHz. */
        if (s_io_ready && !rf_link_ok() && time_reached(next_rf_retry)) {
            next_rf_retry = make_timeout_time_ms(RF_RETRY_MS);
            (void)rf_link_init();
        }
        uint8_t pkt[RF_PAYLOAD_LEN];
        char rf_line[RF_TEXT_MAX + 1];
        bool rf_cmd_now = false;
        if (rf_link_poll(pkt, sizeof pkt, NULL) == (int)RF_PAYLOAD_LEN &&
            rf_unpack(pkt, RF_PAYLOAD_LEN, RF_KIND_CMD, RF_FORKLIFT_ID, rf_line, NULL)) {
            handle_line(rf_line, &last_cmd);
            last_rf_cmd = get_absolute_time();
            rf_cmd_now = true;
        }
        /* Reply slot: wait RF_REPLY_GAP_MS after a command so the base (which needs
           ~1 ms to recalibrate and get back into RX after sending) is listening. */
        if (rf_cmd_now && s_rf_telem_due) sleep_ms(RF_REPLY_GAP_MS);
        if (s_rf_telem_due && rf_link_ok() &&
            (rf_cmd_now || is_nil_time(last_rf_cmd) ||
             absolute_time_diff_us(last_rf_cmd, get_absolute_time()) > (int64_t)RF_QUIET_MS * 1000)) {
            rf_send_telemetry();
        }

        if (s_fork_moving && time_reached(s_fork_until)) {
            fork_brake();
            if (s_state == FL_DRIVING) update_moving_state();
        }

        if ((s_wheels_moving || s_fork_moving) &&
            absolute_time_diff_us(last_cmd, get_absolute_time()) > (int64_t)CMD_TIMEOUT_MS * 1000) {
            all_brake();
            set_state(FL_STOPPED);
            DIAG("[forklift] watchdog: no command for %u ms\n", CMD_TIMEOUT_MS);
        }

        if (!s_io_ready && time_reached(next_retry)) {
            next_retry = make_timeout_time_ms(CONFIG_RETRY_MS);
            try_io_ready();
        }

        if (time_reached(next_telemetry)) {
            next_telemetry = make_timeout_time_ms(TELEMETRY_MS);
            send_telemetry();
            send_status_to_display(s_state);   /* also repaints a display that rebooted */
            send_ip_to_display();
        }

        tight_loop_contents();
    }
}
