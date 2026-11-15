/* Forklift WiFi relay: Bottlenose WiFi Orca (ESP32-C6-MINI-1) on the Free-WiLi OG.
 *
 *   laptop ──WiFi/TCP :5000──► this ESP32 ──I2C (Qwiic)──► OG main CPU (target 0x42)
 *          ◄── T telemetry ──             ◄──             ◄──
 *
 * The laptop sends the same text lines it sends over USB (W / F / S) to TCP port 5000;
 * they are relayed line by line to the OG, which feeds them through the same parser and
 * 500 ms motor watchdog as USB (freewili/forklift/main/wifi_link.h). Telemetry lines the
 * OG queues are read back and sent to the laptop.
 *
 * Safety:
 *   - the laptop disconnects  -> "S,wifi_lost" to the OG at once
 *   - the laptop goes silent  -> the OG's own 500 ms watchdog stops the motors
 *   - this ESP32 crashes/loses power -> no more lines -> same OG watchdog
 *
 * Joins an existing network (secrets.h, not in git). Reachable as forklift-01.local
 * (mDNS) or at the IP shown on the OG's screen (sent to it as "I,<ip>").
 *
 * Qwiic pins: not published for the Bottlenose, so they are FOUND at boot: each likely
 * GPIO pair is tried until the OG acknowledges address 0x42, and the pair is saved in
 * flash (Preferences) so later boots go straight to it. Hold the OG off and it keeps
 * looking (and says so on USB serial, 115200).
 *
 * Build: Arduino ESP32 core 3.x, board "ESP32C6 Dev Module", USB CDC On Boot: Enabled.
 * See freewili/bottlenose/README.md.
 */
#include <ESPmDNS.h>
#include <Preferences.h>
#include <WiFi.h>
#include <Wire.h>

#include "secrets.h"   /* WIFI_SSID, WIFI_PASS (copy secrets.h.example) */

#ifndef HOSTNAME
#define HOSTNAME "forklift-01"
#endif
#ifndef TCP_PORT
#define TCP_PORT 5000
#endif

static const uint8_t OG_ADDR = 0x42;         /* WIFI_LINK_ADDR in wifi_link.h */
static const uint32_t I2C_HZ = 400000;
static const uint32_t POLL_MS = 20;           /* read telemetry from the OG this often */
static const uint32_t IP_EVERY_MS = 2000;     /* re-send the IP so a rebooted OG shows it */
static const size_t LINE_MAX = 64;            /* PROTO_LINE_MAX on the OG */
static const size_t I2C_CHUNK = 32;

/* ESP32-C6 GPIOs worth trying for the Qwiic port. Skipped: 12/13 (USB), 16/17 (UART0
 * console), 24-30 (SPI flash). */
static const uint8_t CANDIDATES[] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 14, 15, 18, 19, 20, 21, 22, 23};

static WiFiServer server(TCP_PORT);
static WiFiClient client;
static Preferences prefs;
static int sda_pin = -1, scl_pin = -1;
static char line[LINE_MAX + 2];
static size_t line_len = 0;
static bool line_overflow = false;
static uint32_t last_poll = 0, last_ip = 0;

static bool og_answers(int sda, int scl) {
    Wire.end();
    if (!Wire.begin(sda, scl, I2C_HZ)) return false;
    Wire.setTimeOut(20);
    Wire.beginTransmission(OG_ADDR);
    return Wire.endTransmission() == 0;
}

static void find_qwiic_pins(void) {
    prefs.begin("forklift", false);
    sda_pin = prefs.getInt("sda", -1);
    scl_pin = prefs.getInt("scl", -1);
    if (sda_pin >= 0 && og_answers(sda_pin, scl_pin)) {
        Serial.printf("[i2c] OG found on saved pins SDA=%d SCL=%d\n", sda_pin, scl_pin);
        return;
    }
    Serial.println("[i2c] looking for the OG (0x42) on every likely pin pair...");
    for (;;) {
        for (uint8_t a : CANDIDATES) {
            for (uint8_t b : CANDIDATES) {
                if (a == b) continue;
                if (og_answers(a, b)) {
                    sda_pin = a;
                    scl_pin = b;
                    prefs.putInt("sda", a);
                    prefs.putInt("scl", b);
                    Serial.printf("[i2c] OG found: SDA=GPIO%d SCL=GPIO%d (saved)\n", a, b);
                    return;
                }
            }
        }
        Serial.println("[i2c] OG not answering on any pair. Qwiic cable to OG header 10 (SDA), "
                       "8 (SCL), 6 (3.3V), 19 (GND)? Forklift firmware with WiFi flashed? Retrying.");
        delay(1000);
    }
}

/* Send bytes to the OG in I2C-sized chunks. */
static bool og_write(const char *s, size_t n) {
    bool ok = true;
    for (size_t i = 0; i < n; i += I2C_CHUNK) {
        const size_t k = (n - i < I2C_CHUNK) ? n - i : I2C_CHUNK;
        Wire.beginTransmission(OG_ADDR);
        Wire.write((const uint8_t *)s + i, k);
        ok &= (Wire.endTransmission() == 0);
    }
    return ok;
}

static void og_write_line(const char *s) {
    if (!og_write(s, strlen(s))) Serial.println("[i2c] write to OG failed");
}

static void send_ip(void) {
    if (WiFi.status() != WL_CONNECTED) return;
    char buf[32];
    snprintf(buf, sizeof buf, "I,%s\n", WiFi.localIP().toString().c_str());
    og_write_line(buf);
}

/* Read queued telemetry from the OG (0x00 = nothing queued) and pass it on. */
static void poll_og(void) {
    uint8_t got[I2C_CHUNK];
    size_t n = 0;
    if (Wire.requestFrom(OG_ADDR, (uint8_t)I2C_CHUNK) == I2C_CHUNK) {
        while (Wire.available()) {
            const int b = Wire.read();
            if (b > 0) got[n++] = (uint8_t)b;
        }
    }
    if (n && client && client.connected()) client.write(got, n);
}

/* Bytes from the laptop: relay each complete line (keeps lines whole on the I2C side). */
static void pump_client(void) {
    while (client.available()) {
        const char c = (char)client.read();
        if (c == '\n') {
            if (!line_overflow && line_len) {
                line[line_len++] = '\n';
                line[line_len] = '\0';
                og_write_line(line);
            }
            line_len = 0;
            line_overflow = false;
        } else if (line_len < LINE_MAX) {
            line[line_len++] = c;
        } else {
            line_overflow = true;   /* drop over-long lines whole, like the OG does */
        }
    }
}

void setup() {
    Serial.begin(115200);
    delay(300);
    Serial.println("\n[forklift-wifi] starting");
    find_qwiic_pins();

    WiFi.mode(WIFI_STA);
    WiFi.setHostname(HOSTNAME);
    WiFi.setAutoReconnect(true);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    Serial.printf("[wifi] joining '%s'", WIFI_SSID);
    while (WiFi.status() != WL_CONNECTED) {
        delay(250);
        Serial.print('.');
    }
    WiFi.setSleep(false);   /* power save adds 100+ ms of latency: too much at 20 Hz */
    Serial.printf("\n[wifi] connected, IP %s\n", WiFi.localIP().toString().c_str());
    if (MDNS.begin(HOSTNAME)) MDNS.addService("forklift", "tcp", TCP_PORT);

    server.begin();
    server.setNoDelay(true);
    Serial.printf("[tcp] listening on %s.local:%d\n", HOSTNAME, TCP_PORT);
    send_ip();
    last_ip = millis();
}

void loop() {
    /* One laptop at a time; a new connection replaces the old one. */
    if (server.hasClient()) {
        WiFiClient incoming = server.accept();
        if (client && client.connected()) {
            og_write_line("S,replaced\n");
            client.stop();
        }
        client = incoming;
        client.setNoDelay(true);
        line_len = 0;
        line_overflow = false;
        Serial.printf("[tcp] laptop connected from %s\n", client.remoteIP().toString().c_str());
    }

    if (client) {
        if (client.connected()) {
            pump_client();
        } else {
            og_write_line("S,wifi_lost\n");     /* stop now, don't wait for the watchdog */
            client.stop();
            Serial.println("[tcp] laptop disconnected: sent S to the OG");
        }
    }

    const uint32_t now = millis();
    if (now - last_poll >= POLL_MS) {
        last_poll = now;
        poll_og();
    }
    if (now - last_ip >= IP_EVERY_MS) {
        last_ip = now;
        send_ip();
    }
    delay(1);
}
