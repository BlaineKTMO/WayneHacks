#include "rf_proto.h"

#include <string.h>

size_t rf_pack(uint8_t out[RF_PAYLOAD_LEN], uint8_t kind, uint8_t id, uint8_t seq,
               const char *line) {
    size_t n = strlen(line);
    while (n && (line[n - 1] == '\n' || line[n - 1] == '\r')) n--;
    if (n == 0) return 0;
    if (n > RF_TEXT_MAX) {
        if (line[0] != 'S') return 0;   /* never send half a W/F line */
        n = RF_TEXT_MAX;                /* a truncated stop reason is still a stop */
    }
    memset(out, 0, RF_PAYLOAD_LEN);
    out[0] = RF_MAGIC;
    out[1] = kind;
    out[2] = id;
    out[3] = seq;
    memcpy(out + RF_HEADER_LEN, line, n);
    return RF_PAYLOAD_LEN;
}

bool rf_unpack(const uint8_t *in, size_t n, uint8_t kind, uint8_t id,
               char line[RF_TEXT_MAX + 1], uint8_t *seq) {
    if (n != RF_PAYLOAD_LEN || in[0] != RF_MAGIC || in[1] != kind || in[2] != id) return false;
    size_t len = 0;
    while (len < RF_TEXT_MAX && in[RF_HEADER_LEN + len] != 0) {
        const uint8_t c = in[RF_HEADER_LEN + len];
        if (c < 0x20 || c > 0x7E) return false;   /* printable text only */
        line[len] = (char)c;
        len++;
    }
    if (len == 0) return false;
    line[len] = '\0';
    if (seq) *seq = in[3];
    return true;
}
