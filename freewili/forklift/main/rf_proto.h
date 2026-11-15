/* Radio packet format between the base station OG (laptop side) and the forklift OG.
 * Pure C: host-tested.
 *
 *   byte 0     RF_MAGIC
 *   byte 1     kind: RF_KIND_CMD (base -> forklift) or RF_KIND_TELEM (forklift -> base)
 *   byte 2     forklift id (RF_FORKLIFT_ID): a second forklift/base pair can share the air
 *   byte 3     sequence number (wraps; diagnostic only, duplicates are harmless)
 *   byte 4..31 the SAME text line as USB, no newline, zero-padded ("W,45,38")
 *
 * Integrity is the CC1101's own CRC: packets that fail it never reach this code. */
#ifndef FORKLIFT_RF_PROTO_H
#define FORKLIFT_RF_PROTO_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define RF_MAGIC 0xF1u
#define RF_KIND_CMD 0x01u
#define RF_KIND_TELEM 0x02u
#define RF_FORKLIFT_ID 0x01u        /* fl-01 */
#define RF_HEADER_LEN 4u
#define RF_TEXT_MAX 28u
#define RF_PAYLOAD_LEN (RF_HEADER_LEN + RF_TEXT_MAX)   /* 32 */

/* Packs `line` (trailing '\r'/'\n' ignored). Lines longer than RF_TEXT_MAX are refused
 * (returns 0), except S lines, which are truncated: a stop must always get through.
 * Returns RF_PAYLOAD_LEN on success. */
size_t rf_pack(uint8_t out[RF_PAYLOAD_LEN], uint8_t kind, uint8_t id, uint8_t seq,
               const char *line);

/* Accepts only a full-length packet with our magic, `kind` and `id`. Writes the text
 * (NUL-terminated, at most RF_TEXT_MAX chars) to `line`. */
bool rf_unpack(const uint8_t *in, size_t n, uint8_t kind, uint8_t id,
               char line[RF_TEXT_MAX + 1], uint8_t *seq);

#endif
