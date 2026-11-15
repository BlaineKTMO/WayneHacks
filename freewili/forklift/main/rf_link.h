/* CC1101 radio link shared by the forklift and the base station (main CPU, radio CS0).
 *
 * 915 MHz (US licence-free 902-928 MHz band), the bring-up defaults (GFSK family,
 * variable-length packets, CRC, RSSI/LQI appended), radio CS1 (the one with a working
 * antenna path on these boards: CS0 measured ~25 dB weaker), +10 dBm. 433 MHz
 * would be the board's default antenna path, but in the US that band only allows
 * intermittent control transmissions, not 20 Hz commands.
 *
 * The antenna path MUST be on 900 MHz before transmitting (wiliOGbsp: radios must not
 * be keyed into an unknown antenna path). The display app sets it at boot
 * (fwog_ioexp_link_set_antennas); call rf_link_init() only after the display has
 * answered on the link, which guarantees that has happened.
 *
 * Variable-length packets with the CC1101's CRC (bad packets auto-flushed) and RSSI/LQI
 * appended. The radio sits in RX except while sending. */
#ifndef FORKLIFT_RF_LINK_H
#define FORKLIFT_RF_LINK_H

#include <stdbool.h>
#include <stdint.h>

#ifdef RF_TEST_433
#define RF_FREQ_HZ 433920000u   /* diagnostic build only (-DFORKLIFT_RF_TEST_433=ON) */
#else
#define RF_FREQ_HZ 915000000u
#endif
/* +10 dBm (the CC1101's maximum). Measured: the OG's antenna path loses ~50 dB, so at
 * -10 dBm a board half a metre away heard the other at about -83 dBm, under the
 * forklift's own -76 dBm noise (motor drivers); at +10 dBm, -63 dBm and packets decode.
 * With that loss the radiated power stays far below +10 dBm. */
#define RF_POWER_DBM 10

bool rf_link_init(void);
bool rf_link_ok(void);

/* Sends one packet (blocks for its airtime, ~9 ms for 32 bytes) and returns to RX. */
bool rf_link_send(const uint8_t *payload, uint8_t len);

/* Non-blocking. If a complete, CRC-good packet has arrived, copies it to `payload`
 * (up to `max` bytes), sets *rssi_dbm, and returns its length; else 0. Keeps the radio
 * in RX. Cheap enough to call every loop (rate-limited inside). */
int rf_link_poll(uint8_t *payload, uint8_t max, int16_t *rssi_dbm);

/* One diagnostic line ("R,...") into buf: counters + live radio registers. Sent in
 * reply to a "?" line on USB (not a T line, so the laptop ignores it). */
void rf_link_report(char *buf, unsigned n);

/* Diagnostics: switch to radio CS0 (0) or CS1 (1) and re-init. "?0" / "?1" on USB. */
void rf_link_select(int which);

#endif
