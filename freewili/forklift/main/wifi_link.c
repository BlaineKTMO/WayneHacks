#include "wifi_link.h"

#include "byte_ring.h"
#include "hardware/gpio.h"
#include "hardware/i2c.h"
#include "pico/i2c_slave.h"

#define PIN_SDA 16u   /* hdr 10 */
#define PIN_SCL 17u   /* hdr 8  */

static byte_ring_t s_rx;   /* ESP32 -> here: pushed by the IRQ, popped by the main loop */
static byte_ring_t s_tx;   /* here -> ESP32: pushed by the main loop, popped by the IRQ */

/* Runs in the I2C interrupt: keep it to ring operations only. */
static void on_i2c(i2c_inst_t *i2c, i2c_slave_event_t event) {
    switch (event) {
    case I2C_SLAVE_RECEIVE:
        (void)byte_ring_push(&s_rx, i2c_read_byte_raw(i2c));   /* full: byte dropped */
        break;
    case I2C_SLAVE_REQUEST: {
        uint8_t b = 0;
        (void)byte_ring_pop(&s_tx, &b);                         /* empty: send 0x00 */
        i2c_write_byte_raw(i2c, b);
        break;
    }
    case I2C_SLAVE_FINISH:
    default:
        break;
    }
}

void wifi_link_init(void) {
    byte_ring_init(&s_rx);
    byte_ring_init(&s_tx);
    gpio_init(PIN_SDA);
    gpio_init(PIN_SCL);
    gpio_set_function(PIN_SDA, GPIO_FUNC_I2C);
    gpio_set_function(PIN_SCL, GPIO_FUNC_I2C);
    /* The PCA9517's 10k pull-ups (on by default) do the real work; the RP2040's weak
       internal ones only keep the lines defined if the Qwiic cable is unplugged. */
    gpio_pull_up(PIN_SDA);
    gpio_pull_up(PIN_SCL);
    i2c_init(i2c0, WIFI_LINK_BAUD);
    i2c_slave_init(i2c0, WIFI_LINK_ADDR, &on_i2c);
}

bool wifi_link_getc(char *c) {
    uint8_t b;
    if (!byte_ring_pop(&s_rx, &b)) return false;
    *c = (char)b;
    return true;
}

void wifi_link_send(const char *s, size_t n) {
    (void)byte_ring_push_all(&s_tx, (const uint8_t *)s, n);
}
