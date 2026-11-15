/* Forklift display: Free-WiLi OG display CPU.
 *
 * Two jobs:
 *   1. Service main's breakout I/O requests over the inter-CPU link. REQUIRED:
 *      without this, main cannot close IO_CONFIG and the motors stay in FAULT.
 *   2. Show the forklift ID and state big enough to read on the demo camera.
 *
 * Red held 6 s powers the board off (BSP default power policy).
 */
#include "fwog_display.h"
#include "pico/stdlib.h"

#include <stdio.h>
#include <string.h>

FWOG_POWER_DEFAULT();

/* Upper case on screen only: in this font a lowercase l reads as a 1. The ID
 * everywhere else (MQTT, DynamoDB, dashboard) stays "fl-01". */
#ifdef BASE_STATION
#define FORKLIFT_ID "BASE"          /* radio base station: shows the forklift's state */
#else
#define FORKLIFT_ID "FL-01"
#endif
#define MSG_FORKLIFT_STATUS 0x40u   /* keep in step with forklift/main/main.c */
#define MSG_FORKLIFT_IP 0x41u       /* [0x41, ascii IP]: the WiFi module's address */
#define IP_MAX 20u
#define MAIN_SILENT_MS 3000u        /* main sends status every second */

/* Same order as fl_state_t in main/motor_proto.h. */
/* Index 5 is the base station's own: nothing heard from the forklift over the radio. */
static const char *const STATE_NAMES[] = {"IDLE", "DRIVING", "BLOCKED", "STOPPED", "FAULT",
                                          "NO RADIO"};
#define N_STATES (sizeof STATE_NAMES / sizeof STATE_NAMES[0])

static fwog_link_rx_t s_link_rx;    /* ~4 KB: static, never on the stack */
static bool s_lcd_ready;

static uint16_t state_colour(int s) {
    switch (s) {
    case 0:  return st7789_rgb565(255, 255, 255);  /* IDLE white */
    case 1:  return st7789_rgb565(0, 255, 0);      /* DRIVING green */
    case 2:  return st7789_rgb565(255, 255, 0);    /* BLOCKED yellow */
    case 3:  return st7789_rgb565(255, 128, 0);    /* STOPPED orange */
    default: return st7789_rgb565(255, 0, 0);      /* FAULT / unknown red */
    }
}

/* lcd_text draws NOTHING above scale 3 (LCD_MAX_SCALE in the BSP's lcd_text.c),
 * silently. At scale 3 a glyph is 18x24 px on the 320x240 panel. */
#define TEXT_SCALE 3u
#define TEXT_X 16u
#define ID_Y 70u
#define STATE_Y 140u
#define IP_Y 200u
#define IP_SCALE 2u                 /* 12 x 16 px glyphs: "192.168.100.100" fits */

static void draw_state(const char *text, uint16_t colour) {
    if (!s_lcd_ready) return;
    lcd_text_draw_padded(TEXT_X, STATE_Y, text, 9u, TEXT_SCALE, colour, 0x0000u);
}

int main(void) {
    board_init();

    /* The bootloader deinits the link before jumping here; bring it back up. */
    const bool link_up = fwog_link_uart_init(FWOG_LINK_BAUD);
    fwog_link_rx_init(&s_link_rx);
    fwog_buttons_init();

    /* Both CC1101s use the 915 MHz band (main/rf_link.h): switch their antenna paths
       from the 400 MHz default BEFORE main may transmit. main only starts the radio
       after this app answers on the link, which is below. */
    #ifdef RF_TEST_433
    (void)fwog_ioexp_link_set_antennas(FWOG_ANT_400MHZ, FWOG_ANT_400MHZ);   /* diagnostic build */
#else
    (void)fwog_ioexp_link_set_antennas(FWOG_ANT_900MHZ, FWOG_ANT_900MHZ);
#endif

    /* The OG runs on its internal battery and charges it from USB. Same charger setup
       the stock firmware and the BSP's bench app do (enable charging, ADC on, charger
       watchdog off). Never touches ship mode. */
    (void)bq25896_start_charging();

    /* Bounded: st7789 init can stall forever if clk_peri is wrong, and this CPU
       has no watchdog to recover from a spin. */
    st7789_init_begin();
    const absolute_time_t lcd_deadline = make_timeout_time_ms(500);
    while (!st7789_ready() && !time_reached(lcd_deadline)) st7789_init_step();
    s_lcd_ready = st7789_ready();
    if (s_lcd_ready) {
        st7789_clear(0x0000u);
        st7789_dma_wait();
        board_backlight(255);
        lcd_text_draw_padded(TEXT_X, ID_Y, FORKLIFT_ID, 9u, TEXT_SCALE, 0xFFFFu, 0x0000u);
        draw_state(link_up ? "WAITING" : "NO LINK", state_colour(link_up ? 0 : 4));
    }

    int shown = -1;
    char ip_shown[IP_MAX + 1] = "";
    absolute_time_t last_status = get_absolute_time();
    bool silent_shown = false;

    while (true) {
        fwog_power_poll(to_ms_since_boot(get_absolute_time()));

        uint8_t b;
        while (link_up && fwog_link_uart_read(&b)) {
            size_t len = 0u;
            if (!fwog_link_rx_byte(&s_link_rx, b, &len)) continue;
            if (fwog_ioexp_link_handle(s_link_rx.buf, len)) continue;   /* breakout I/O, acked */
            if (len >= 2u && s_link_rx.buf[0] == MSG_FORKLIFT_IP && s_lcd_ready) {
                char ip[IP_MAX + 1];
                size_t n = len - 1u > IP_MAX ? IP_MAX : len - 1u;
                memcpy(ip, s_link_rx.buf + 1, n);
                ip[n] = '\0';
                if (strcmp(ip, ip_shown) != 0) {
                    strcpy(ip_shown, ip);
                    lcd_text_draw_padded(TEXT_X, IP_Y, ip, IP_MAX, IP_SCALE,
                                         st7789_rgb565(0, 200, 255), 0x0000u);
                }
                continue;
            }
            if (len >= 2u && s_link_rx.buf[0] == MSG_FORKLIFT_STATUS) {
                const int s = s_link_rx.buf[1];
                last_status = get_absolute_time();
                silent_shown = false;
                if (s != shown) {
                    shown = s;
                    draw_state((s >= 0 && (size_t)s < N_STATES) ? STATE_NAMES[s] : "???",
                               state_colour(s));
                }
            }
        }

        if (!silent_shown &&
            absolute_time_diff_us(last_status, get_absolute_time()) > (int64_t)MAIN_SILENT_MS * 1000) {
            draw_state("NO MAIN", state_colour(4));
            shown = -1;
            silent_shown = true;
        }

        sleep_ms(2);   /* poll fast: the power-button debouncer needs it */
    }
}
