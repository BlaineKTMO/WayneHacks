/* Radio packet format between the base station OG (laptop side) and the forklift OG.
 * Pure C: host-tested.
 *
 *   byte 0     RF_MAGIC
 *   byte 1     kind: RF_KIND_CMD or RF_KIND_POLL (base -> forklift), RF_KIND_TELEM (back)
 *   byte 2     forklift id (RF_FORKLIFT_ID): one base drives several forklifts
 *   byte 3     sequence number (wraps; diagnostic only, duplicates are harmless)
 *   byte 4..31 the SAME text line as USB, no newline, zero-padded ("W,45,38")
 *
 * Integrity is the CC1101's own CRC: packets that fail it never reach this code.
 *
 * Who talks when (one base, several forklifts, one channel): a forklift sends its
 * telemetry only right after a POLL addressed to it (a command that also says "your
 * turn to answer"), and the base sends nothing else until that answer has arrived or
 * RF_SLOT_MS has passed. So two forklifts never answer over each other or over the base.
 *
 * On the laptop's USB side the base takes "<id>:<line>" ("2:W,45,38"); a line with no
 * prefix goes to forklift 1, so a single-forklift setup sends exactly what it always did.
 * Telemetry comes back the same way: forklift 1 as plain "T,...", others as "2:T,...". */
#ifndef FORKLIFT_RF_PROTO_H
#define FORKLIFT_RF_PROTO_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define RF_MAGIC 0xF1u
#define RF_KIND_CMD 0x01u
#define RF_KIND_TELEM 0x02u
#define RF_KIND_POLL 0x03u          /* a command that also asks for the telemetry reply */
#ifndef RF_FORKLIFT_ID
#define RF_FORKLIFT_ID 0x01u        /* fl-01; the fl02 build sets 2 (CMakeLists.txt) */
#endif
#define RF_DEFAULT_ID 0x01u         /* USB lines with no "<id>:" prefix */
#define RF_MAX_ID 9u
#define RF_SLOT_MS 25u              /* a polled forklift's reply window (5 ms gap + ~9 ms air) */
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

/* The forklift id in a packet's header, or 0 if it isn't one of our packets. */
uint8_t rf_packet_id(const uint8_t *in, size_t n);

/* "2:W,45,38" -> *id = 2, returns "W,45,38". No (valid) prefix -> *id = RF_DEFAULT_ID,
 * returns `line` unchanged. Valid ids are 1..RF_MAX_ID. */
const char *rf_split_addr(const char *line, uint8_t *id);

#endif
