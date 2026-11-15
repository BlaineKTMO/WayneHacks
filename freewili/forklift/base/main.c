/* Forklift radio BASE STATION: a second Free-WiLi OG, plugged into the laptop by USB.
 *
 *   laptop --USB lines--> this OG --CC1101 915 MHz--> forklift OG (rf_proto.h packets)
 *          <--T lines---          <--telemetry--
 *
 * To the laptop it looks just like a forklift on USB: send W / F / S lines, get T lines
 * back. Command lines go out over the radio as they arrive; anything else is ignored,
 * exactly as the forklift ignores it. Telemetry from the forklift comes back as its own
 * T line. If nothing has been heard from the forklift for NO_RADIO_MS, this prints
 * "T,NO_RADIO,0.00" once a second instead (and "T,RADIO_FAULT,0.00" if this board's
 * radio won't start).
 *
 * The forklift's 500 ms watchdog still protects everything: if the laptop, this board or
 * the radio link stops, commands stop arriving and the forklift brakes.
 *
 * Screen: "BASE" and the forklift's state as heard over the radio. */
#include "fwog_main.h"
#include "hardware/uart.h"
#include "pico/stdlib.h"

#include <stdio.h>
#include <string.h>

#include "../main/motor_proto.h"
#include "../main/rf_link.h"
#include "../main/rf_proto.h"

FWOG_WATCHDOG_DEFAULT();

#define MSG_FORKLIFT_STATUS 0x40u   /* same display protocol as the forklift */
#define DISPLAY_NO_RADIO 5u         /* index of "NO RADIO" in the display's state list */
#define DISPLAY_RADIO_FAULT 4u      /* FAULT */
#define NO_RADIO_MS 3000u
#define STATUS_MS 1000u
#define RF_RETRY_MS 2000u
#define DISPLAY_WAIT_RETRY_MS 500u

static uint8_t s_shown = 0xFFu;

static void show(uint8_t state_index) {
    if (!(uart_get_hw(uart0)->fr & UART_UARTFR_TXFE_BITS)) return;   /* never block */
    const uint8_t payload[2] = {MSG_FORKLIFT_STATUS, state_index};
    (void)fwog_link_uart_send_frame(payload, sizeof payload);
    s_shown = state_index;
}

/* "T,DRIVING,0.00" -> the display's index for DRIVING (fl_state_t order). */
static uint8_t state_index_of(const char *t_line) {
    static const char *const names[] = {"IDLE", "DRIVING", "BLOCKED", "STOPPED", "FAULT"};
    for (uint8_t i = 0; i < 5; i++) {
        const size_t k = strlen(names[i]);
        if (strncmp(t_line + 2, names[i], k) == 0 && t_line[2 + k] == ',') return i;
    }
    return DISPLAY_RADIO_FAULT;
}

static void usb_line(const char *s) {
    fputs(s, stdout);
    fputc('\n', stdout);
    fflush(stdout);
}

int main(void) {
    board_init();
    const fwog_display_result_t d = fwog_display_update_run();
    DIAG("[base] display: %s\n", fwog_display_result_text(d));

    /* Wait until the display app answers on the link: by then it has switched the
       antenna paths to 900 MHz, so the radio may transmit. (Closing IO_CONFIG is just
       the handshake used to ask; this board drives no breakout pins.) */
    absolute_time_t next_try = get_absolute_time();
    bool display_up = false;
    while (!display_up) {
        board_watchdog_kick();
        if (time_reached(next_try)) {
            next_try = make_timeout_time_ms(DISPLAY_WAIT_RETRY_MS);
            display_up = fwog_io_config_window(false);
        }
    }
    (void)rf_link_init();

    line_rx_t rx;
    line_rx_init(&rx);
    uint8_t seq = 0;
    absolute_time_t last_heard = nil_time;
    absolute_time_t next_status = make_timeout_time_ms(STATUS_MS);
    absolute_time_t next_rf_retry = make_timeout_time_ms(RF_RETRY_MS);

    while (true) {
        board_watchdog_kick();

        /* Laptop -> radio: command lines only, one packet each. */
        int ch;
        while ((ch = getchar_timeout_us(0)) != PICO_ERROR_TIMEOUT) {
            if (!line_rx_feed(&rx, (char)ch)) continue;
            if (rx.buf[0] == '?' && (rx.buf[1] == '0' || rx.buf[1] == '1') && rx.buf[2] == '\0') {
                rf_link_select(rx.buf[1] - '0');           /* diagnostics: pick radio */
                continue;
            }
            if (rx.buf[0] == '?' && rx.buf[1] == '\0') {   /* radio diagnostics */
                char r[200];
                rf_link_report(r, sizeof r);
                fputs(r, stdout);
                fflush(stdout);
                continue;
            }
            if (proto_parse_line(rx.buf).kind == CMD_NONE) continue;
            uint8_t pkt[RF_PAYLOAD_LEN];
            if (rf_pack(pkt, RF_KIND_CMD, RF_FORKLIFT_ID, seq++, rx.buf))
                (void)rf_link_send(pkt, RF_PAYLOAD_LEN);
        }

        /* Radio -> laptop: the forklift's telemetry, verbatim. */
        uint8_t pkt[RF_PAYLOAD_LEN];
        char line[RF_TEXT_MAX + 1];
        int16_t rssi = 0;
        if (rf_link_poll(pkt, sizeof pkt, &rssi) == (int)RF_PAYLOAD_LEN &&
            rf_unpack(pkt, RF_PAYLOAD_LEN, RF_KIND_TELEM, RF_FORKLIFT_ID, line, NULL) &&
            line[0] == 'T' && line[1] == ',') {
            usb_line(line);
            last_heard = get_absolute_time();
            show(state_index_of(line));
            DIAG("[base] heard forklift, RSSI %d dBm\n", (int)rssi);
        }

        if (!rf_link_ok() && time_reached(next_rf_retry)) {
            next_rf_retry = make_timeout_time_ms(RF_RETRY_MS);
            (void)rf_link_init();
        }

        if (time_reached(next_status)) {
            next_status = make_timeout_time_ms(STATUS_MS);
            if (!rf_link_ok()) {
                usb_line("T,RADIO_FAULT,0.00");
                show(DISPLAY_RADIO_FAULT);
            } else if (is_nil_time(last_heard) ||
                       absolute_time_diff_us(last_heard, get_absolute_time()) >
                           (int64_t)NO_RADIO_MS * 1000) {
                usb_line("T,NO_RADIO,0.00");
                show(DISPLAY_NO_RADIO);
            } else if (s_shown != 0xFFu) {
                show(s_shown);   /* repaint a display that rebooted */
            }
        }
        tight_loop_contents();
    }
}
