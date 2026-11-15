/* Single-producer / single-consumer byte ring, safe between one interrupt handler and
 * the main loop (each side only writes its own index). Pure C: host-tested.
 *
 * Used by the WiFi link: the I2C interrupt pushes received bytes and pops bytes to send;
 * the main loop does the other half. */
#ifndef FORKLIFT_BYTE_RING_H
#define FORKLIFT_BYTE_RING_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define BYTE_RING_SIZE 256u    /* power of two; holds BYTE_RING_SIZE - 1 bytes */

typedef struct {
    uint8_t buf[BYTE_RING_SIZE];
    volatile uint16_t head;    /* next write; written only by the producer */
    volatile uint16_t tail;    /* next read;  written only by the consumer */
} byte_ring_t;

void byte_ring_init(byte_ring_t *r);
bool byte_ring_push(byte_ring_t *r, uint8_t b);        /* false (byte dropped) if full */
bool byte_ring_pop(byte_ring_t *r, uint8_t *b);        /* false if empty */
size_t byte_ring_count(const byte_ring_t *r);
size_t byte_ring_free(const byte_ring_t *r);

/* Push all of `n` bytes, or none of them (so a telemetry line is never cut in half). */
bool byte_ring_push_all(byte_ring_t *r, const uint8_t *p, size_t n);

#endif
