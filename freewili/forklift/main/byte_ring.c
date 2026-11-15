#include "byte_ring.h"

#define MASK (BYTE_RING_SIZE - 1u)

void byte_ring_init(byte_ring_t *r) {
    r->head = 0;
    r->tail = 0;
}

size_t byte_ring_count(const byte_ring_t *r) {
    return (size_t)((uint16_t)(r->head - r->tail) & MASK);
}

size_t byte_ring_free(const byte_ring_t *r) {
    return (BYTE_RING_SIZE - 1u) - byte_ring_count(r);
}

bool byte_ring_push(byte_ring_t *r, uint8_t b) {
    const uint16_t h = r->head;
    const uint16_t next = (uint16_t)((h + 1u) & MASK);
    if (next == r->tail) return false;
    r->buf[h] = b;
    r->head = next;            /* publish after the byte is stored */
    return true;
}

bool byte_ring_pop(byte_ring_t *r, uint8_t *b) {
    const uint16_t t = r->tail;
    if (t == r->head) return false;
    *b = r->buf[t];
    r->tail = (uint16_t)((t + 1u) & MASK);
    return true;
}

bool byte_ring_push_all(byte_ring_t *r, const uint8_t *p, size_t n) {
    if (n > byte_ring_free(r)) return false;
    for (size_t i = 0; i < n; i++) (void)byte_ring_push(r, p[i]);
    return true;
}
