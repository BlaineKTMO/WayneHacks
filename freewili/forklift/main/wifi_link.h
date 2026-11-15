/* WiFi link: the Bottlenose (ESP32-C6) talks to this CPU over I2C, Qwiic cable to the
 * breakout header. This CPU is the I2C TARGET (slave); the ESP32 is the controller.
 *
 *   OG header pin 10 = SDA (GPIO 16), pin 8 = SCL (GPIO 17), pin 6 = 3.3 V, pin 19/20 = GND
 *   Address WIFI_LINK_ADDR (7-bit).
 *
 * Wire format, deliberately the same text as USB so one parser serves both:
 *   controller WRITE: raw bytes of command lines ("W,45,38\n", "F,60,300\n", "S,x\n",
 *                     "I,192.168.1.42\n" = the ESP32's IP, shown on the OG's screen)
 *   controller READ:  bytes of queued telemetry lines ("T,IDLE,0.00\n"); 0x00 when
 *                     nothing is queued (the ESP32 drops zeros)
 *
 * Breakout I2C pins have no direction bit and are buffered by a PCA9517 whose pull-ups
 * the board enables by default (wiliOGbsp io_pins.h), so this needs no IO_CONFIG step. */
#ifndef FORKLIFT_WIFI_LINK_H
#define FORKLIFT_WIFI_LINK_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define WIFI_LINK_ADDR 0x42u
#define WIFI_LINK_BAUD 400000u

void wifi_link_init(void);

/* Next received byte, false if none. Main loop only. */
bool wifi_link_getc(char *c);

/* Queue a whole line for the ESP32 to read; dropped whole if it doesn't fit (the ESP32
 * isn't reading, e.g. not connected). Main loop only. */
void wifi_link_send(const char *s, size_t n);

#endif
