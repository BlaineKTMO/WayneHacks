/* Forklift motor controller: Free-WiLi OG main CPU.
 *
 * The edge laptop sends W/S lines over USB serial; this drives an L298N from the
 * breakout header and reports T lines once a second. Protocol: motor_proto.h.
 *
 * Pins (all are outputs in the board's DEFAULT direction config, so this app never
 * changes a direction -- that BSP path has not been verified on hardware):
 *
 *   L298N  header  GPIO  use
 *   ENA    hdr 9    8    left speed, PWM slice 4A
 *   IN1    hdr 3   27    left direction
 *   IN2    hdr 1   13    left direction   (FPGA SPI group)
 *   ENB    hdr 11  11    right speed, PWM slice 5B
 *   IN3    hdr 15  14    right direction  (FPGA SPI group)
 *   IN4    hdr 13  15    right direction  (FPGA SPI group)
 *
 * Header pin 4 (V PINS IN) must be powered (jumper hdr 6 -> hdr 4) or no pin moves.
 * Remove the L298N's ENA/ENB jumpers so the EN pins follow the PWM.
 */
#include "fwog_main.h"
#include "hardware/clocks.h"
#include "hardware/pwm.h"
#include "hardware/uart.h"
#include "pico/stdlib.h"

#include <stdio.h>

#include "motor_proto.h"

/* Kicks the BSP's 8.3 s board watchdog from the loop below. The 500 ms motor
 * watchdog (CMD_TIMEOUT_MS) is separate and much stricter. */
FWOG_WATCHDOG_DEFAULT();

#define PIN_ENA 8u
#define PIN_IN1 27u
#define PIN_IN2 13u
#define PIN_ENB 11u
#define PIN_IN3 14u
#define PIN_IN4 15u

#define PWM_HZ 10000u          /* 10 kHz: quiet enough, well inside the L298N's range */
#define PWM_WRAP 999u
#define CMD_TIMEOUT_MS 500u    /* no valid W/S line for this long -> motors off */
#define TELEMETRY_MS 1000u
#define CONFIG_RETRY_MS 1000u

/* main -> display, payload [MSG_FORKLIFT_STATUS, fl_state_t]. 0x01-0x1F belong to the
 * bootloader and 0x20-0x22 to the breakout I/O protocol (bsp/common/link/). */
#define MSG_FORKLIFT_STATUS 0x40u

/* The OG cannot measure the motor battery (the breakout lines are digital), so the
 * telemetry field is reported as 0.00 until a sensor is added. */
#define BATTERY_V_UNKNOWN 0.0f

static fl_state_t s_state = FL_FAULT;
static bool s_io_ready;        /* IO_CONFIG known-closed: the SPI-group pins may be driven */
static bool s_energized;       /* EN pins are non-zero (driving or braking) */

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

static void set_duty(unsigned gpio, uint8_t pct) {
    pwm_set_gpio_level(gpio, (uint16_t)((uint32_t)pct * (PWM_WRAP + 1u) / 100u));
}

/* EN low on both channels: the L298N lets the motors coast, whatever IN1-4 say. */
static void motors_off(void) {
    set_duty(PIN_ENA, 0);
    set_duty(PIN_ENB, 0);
    s_energized = false;
}

static bool dir_pin(unsigned gpio, bool level) {
    /* Re-asserted on every call on purpose (breakout.h: FPGA traffic can leave
       GPIO 12/14/15 as inputs). Refuses the SPI group unless IO_CONFIG is closed. */
    return fwog_io_pin_drive(gpio, level) == FWOG_IO_PIN_OK;
}

static bool apply_wheels(int left, int right) {
    const l298n_channel_t a = l298n_channel(left);
    const l298n_channel_t b = l298n_channel(right);
    if (!(dir_pin(PIN_IN1, a.in1) && dir_pin(PIN_IN2, a.in2) &&
          dir_pin(PIN_IN3, b.in1) && dir_pin(PIN_IN4, b.in2))) {
        motors_off();
        return false;
    }
    set_duty(PIN_ENA, a.duty_pct);
    set_duty(PIN_ENB, b.duty_pct);
    s_energized = true;
    return true;
}

/* Best effort, and NEVER blocking. fwog_link_uart_send_frame() is a blocking write
 * on a flow-controlled UART, so if the display stopped reading, this CPU would
 * hang inside it and the 500 ms motor watchdog would never run. A 7-byte frame
 * always fits the 32-byte TX FIFO, so send only when the FIFO is empty. */
static void send_status_to_display(void) {
    if (!(uart_get_hw(uart0)->fr & UART_UARTFR_TXFE_BITS)) return;
    const uint8_t payload[2] = {MSG_FORKLIFT_STATUS, (uint8_t)s_state};
    (void)fwog_link_uart_send_frame(payload, sizeof payload);
}

static void set_state(fl_state_t s) {
    if (s == s_state) return;
    s_state = s;
    send_status_to_display();
}

static void send_telemetry(void) {
    char buf[32];
    const size_t n = proto_format_telemetry(buf, sizeof buf, s_state, BATTERY_V_UNKNOWN);
    fwrite(buf, 1, n, stdout);
    fflush(stdout);
}

/* Close the FPGA's IO_CONFIG window so the SPI-group direction pins may be driven.
 * Needs the display app to be servicing the link (forklift/display/main.c). */
static void try_io_ready(void) {
    s_io_ready = fwog_io_config_window(false) &&
                 fwog_io_config_state() == FWOG_IO_CONFIG_DISABLED;
    if (s_io_ready) {
        DIAG("[forklift] breakout ready\n");
        set_state(FL_IDLE);
    } else {
        DIAG("[forklift] IO_CONFIG close not acked by display; retrying\n");
        set_state(FL_FAULT);
    }
}

static void handle_line(const char *line, absolute_time_t *last_cmd) {
    const cmd_t c = proto_parse_line(line);
    switch (c.kind) {
    case CMD_WHEELS:
        *last_cmd = get_absolute_time();
        if (!s_io_ready) return;                 /* FAULT: stay off */
        if (apply_wheels(c.left, c.right)) {
            set_state((c.left || c.right) ? FL_DRIVING : FL_IDLE);
        } else {
            DIAG("[forklift] direction pin refused; motors off\n");
            s_io_ready = false;                  /* re-check IO_CONFIG */
            set_state(FL_FAULT);
        }
        break;
    case CMD_STOP:
        *last_cmd = get_absolute_time();
        motors_off();
        if (s_io_ready) set_state(FL_STOPPED);
        break;
    case CMD_NONE:
        break;                                   /* garbage never feeds the watchdog */
    }
}

int main(void) {
    board_init();

    /* Motors provably off before anything else: EN pins are not in the FPGA SPI
       group, so they can be claimed immediately. */
    pwm_pin_init(PIN_ENA);
    pwm_pin_init(PIN_ENB);
    motors_off();

    /* Brings the display CPU up (and reflashes it if its image changed). Performs
       board_release_display() itself -- do not call it separately. */
    const fwog_display_result_t d = fwog_display_update_run();
    DIAG("[forklift] display: %s\n", fwog_display_result_text(d));

    try_io_ready();

    line_rx_t rx;
    line_rx_init(&rx);
    absolute_time_t last_cmd = get_absolute_time();
    absolute_time_t next_telemetry = make_timeout_time_ms(TELEMETRY_MS);
    absolute_time_t next_retry = make_timeout_time_ms(CONFIG_RETRY_MS);

    while (true) {
        board_watchdog_kick();

        int ch;
        while ((ch = getchar_timeout_us(0)) != PICO_ERROR_TIMEOUT) {
            if (line_rx_feed(&rx, (char)ch)) handle_line(rx.buf, &last_cmd);
        }

        if (s_energized &&
            absolute_time_diff_us(last_cmd, get_absolute_time()) > (int64_t)CMD_TIMEOUT_MS * 1000) {
            motors_off();
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
            send_status_to_display();            /* also repaints a display that rebooted */
        }

        tight_loop_contents();
    }
}
