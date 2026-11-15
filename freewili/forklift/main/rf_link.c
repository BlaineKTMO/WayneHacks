#include "rf_link.h"

#include "fwog_main.h"
#include "pico/stdlib.h"
#include "radio/cc1101.h"

#include <stdio.h>

#define RADIO_SPI_HZ 1000000u      /* the reference's conservative 1 MHz */
#define POLL_EVERY_US 1000u
#define MARC_IDLE 0x01u
#define MARC_RX 0x0Du
#define MARC_RXFIFO_OVERFLOW 0x11u
#define MAX_PKT 61u                /* variable-length mode maximum */
#ifndef RF_RADIO
#define RF_RADIO CC1101_RADIO_CS1  /* the OG has two CC1101s; this is the one we use */
#endif

static cc1101_t s_radio;
static bool s_ok;
static absolute_time_t s_next_poll;
static unsigned s_tx_ok, s_tx_fail, s_rx_ok, s_rx_bad, s_inits;
static int s_which = RF_RADIO;
static int16_t s_rssi_max = -200;

static bool configure(void) {
    /* The BSP bench's verified setup (apps/bench "rf": bring-up bank + frequency +
       power; variable-length packets, CRC on, RSSI/LQI appended) plus GFSK. */
    bool ok = cc1101_bringup(&s_radio) && cc1101_probe(&s_radio);
    ok = ok && cc1101_set_frequency(&s_radio, RF_FREQ_HZ);
    /* The bring-up bank's modulation is OOK (MDMCFG2 0x32), which lost about half the
       packets at -40 dBm. GFSK is more robust. Only the modulation is changed: with the
       driver's data-rate / deviation / RX-bandwidth setters as well (38.4 kbaud, 20 kHz,
       101 kHz) NOTHING decoded at -46 dBm, so the bring-up bank's values are kept. */
    ok = ok && cc1101_set_modulation(&s_radio, CC1101_MOD_GFSK);
    ok = ok && cc1101_set_power(&s_radio, RF_POWER_DBM);   /* after modulation: PA table */
    ok = ok && cc1101_idle(&s_radio) && cc1101_flush_rx(&s_radio) && cc1101_rx(&s_radio);
    return ok;
}

bool rf_link_init(void) {
    static bool bus_up;
    if (!bus_up) {
        /* board_init() already parked BOTH chip selects high (cc1101_bind needs it). */
        cc1101_bus_init(RADIO_SPI_HZ);
        cc1101_bind(&s_radio, (cc1101_radio_t)s_which);
        bus_up = true;
    }
    s_inits++;
    s_ok = configure();
    s_next_poll = get_absolute_time();
    DIAG("[rf] %s: %u Hz, %d dBm\n", s_ok ? "ready" : "INIT FAILED",
         (unsigned)RF_FREQ_HZ, RF_POWER_DBM);
    return s_ok;
}

bool rf_link_ok(void) { return s_ok; }

static void back_to_rx(void) {
    (void)cc1101_idle(&s_radio);
    (void)cc1101_flush_rx(&s_radio);
    (void)cc1101_rx(&s_radio);
}

bool rf_link_send(const uint8_t *payload, uint8_t len) {
    if (!s_ok) return false;
    (void)cc1101_idle(&s_radio);
    const bool sent = cc1101_send_packet(&s_radio, payload, len);
    if (sent) s_tx_ok++; else s_tx_fail++;
    back_to_rx();
    return sent;
}

int rf_link_poll(uint8_t *payload, uint8_t max, int16_t *rssi_dbm) {
    if (!s_ok || !time_reached(s_next_poll)) return 0;
    s_next_poll = make_timeout_time_us(POLL_EVERY_US);

    uint8_t marc = 0;
    if (!cc1101_read_status(&s_radio, CC1101_STATUS_MARCSTATE, &marc)) return 0;
    marc &= 0x1Fu;
    if (marc == MARC_RX) {                               /* listening or mid-packet */
        const int16_t r = cc1101_get_rssi(&s_radio);
        if (r > s_rssi_max) s_rssi_max = r;
        return 0;
    }
    if (marc == MARC_RXFIFO_OVERFLOW) { back_to_rx(); return 0; }

    /* A finished packet leaves the radio IDLE (RXOFF_MODE) with it in the FIFO:
       [length][payload ...][RSSI][LQI|CRC_OK]. A CRC failure was auto-flushed. */
    int got = 0;
    const int raw = cc1101_rx_bytes_available(&s_radio);
    const int n = (raw < 0) ? 0 : (raw & 0x7F);
    if (marc == MARC_IDLE && n >= 3) {
        uint8_t buf[MAX_PKT + 3];
        const int take = n > (int)sizeof buf ? (int)sizeof buf : n;
        if (cc1101_receive_packet(&s_radio, buf, (uint8_t)take) == take) {
            const int len = buf[0];
            if (len >= 1 && len + 3 <= take && len <= max && cc1101_lqi_crc_ok(buf[len + 2])) {
                for (int i = 0; i < len; i++) payload[i] = buf[1 + i];
                if (rssi_dbm) *rssi_dbm = cc1101_rssi_dbm((int8_t)buf[len + 1]);
                got = len;
                s_rx_ok++;
            } else {
                s_rx_bad++;
            }
        }
    }
    if (marc == MARC_IDLE || got) back_to_rx();
    return got;
}

void rf_link_select(int which) {
    s_which = which ? CC1101_RADIO_CS1 : CC1101_RADIO_CS0;
    cc1101_bind(&s_radio, (cc1101_radio_t)s_which);
    (void)rf_link_init();
}

void rf_link_report(char *buf, unsigned n) {
    uint8_t marc = 0, part = 0, ver = 0, f2 = 0, f1 = 0, f0 = 0, pk0 = 0, pk1 = 0, md2 = 0,
            s1 = 0, s0 = 0, io0 = 0, rxb = 0;
    (void)cc1101_read_status(&s_radio, CC1101_STATUS_MARCSTATE, &marc);
    (void)cc1101_read_status(&s_radio, CC1101_STATUS_PARTNUM, &part);
    (void)cc1101_read_status(&s_radio, CC1101_STATUS_VERSION, &ver);
    (void)cc1101_read_status(&s_radio, CC1101_STATUS_RXBYTES, &rxb);
    (void)cc1101_read_reg(&s_radio, CC1101_REG_FREQ2, &f2);
    (void)cc1101_read_reg(&s_radio, CC1101_REG_FREQ1, &f1);
    (void)cc1101_read_reg(&s_radio, CC1101_REG_FREQ0, &f0);
    (void)cc1101_read_reg(&s_radio, CC1101_REG_PKTCTRL0, &pk0);
    (void)cc1101_read_reg(&s_radio, CC1101_REG_PKTCTRL1, &pk1);
    (void)cc1101_read_reg(&s_radio, CC1101_REG_MDMCFG2, &md2);
    (void)cc1101_read_reg(&s_radio, CC1101_REG_SYNC1, &s1);
    (void)cc1101_read_reg(&s_radio, CC1101_REG_SYNC0, &s0);
    (void)cc1101_read_reg(&s_radio, CC1101_REG_IOCFG0, &io0);
    snprintf(buf, n,
             "R,cs=%d,peak_rssi=%d,ok=%d,inits=%u,tx=%u,txfail=%u,rx=%u,rxbad=%u,marc=0x%02X,rxbytes=0x%02X,"
             "rssi=%d,part=0x%02X,ver=0x%02X,freq=%02X%02X%02X,pk0=0x%02X,pk1=0x%02X,"
             "md2=0x%02X,sync=%02X%02X,io0=0x%02X,gdo0=%d\n",
             s_which, (int)s_rssi_max, (int)s_ok, s_inits, s_tx_ok, s_tx_fail, s_rx_ok, s_rx_bad, marc & 0x1Fu, rxb,
             (int)cc1101_get_rssi(&s_radio), part, ver, f2, f1, f0, pk0, pk1, md2, s1, s0, io0,
             (int)cc1101_get_gdo0(&s_radio));
    s_rssi_max = -200;
}
