/* Host test for display/vtext.c: rotated glyphs land where a reader of the turned
 * screen expects them. */
#include <stdio.h>
#include <string.h>
#include "../display/vtext.h"

static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

/* Upright glyph pixel (gx, gy) of a 6x8 glyph, scale 1. */
static int upright(const uint8_t *cols, unsigned gx, unsigned gy) { return (cols[gx] >> gy) & 1; }

/* Where logical (portrait) point (lx, ly) lands on the 320 x 240 panel. */
static void to_panel(unsigned lx, unsigned ly, unsigned rot, unsigned *px, unsigned *py) {
    if (rot == 90u) { *px = ly; *py = VTEXT_PANEL_H - 1u - lx; }
    else            { *px = VTEXT_PANEL_W - 1u - ly; *py = lx; }
}

int main(void) {
    const uint8_t g[6] = {0x01, 0x03, 0x80, 0x00, 0x7F, 0x55};   /* asymmetric test glyph */
    uint16_t out[6 * 8];
    for (unsigned rot = 90u; rot <= 270u; rot += 180u) {
        const unsigned lx = 30u, ly = 100u;
        unsigned x, y, ww, hh;
        vtext_window(lx, ly, 6u, 8u, rot, &x, &y, &ww, &hh);
        CHECK(ww == 8u && hh == 6u);
        CHECK(vtext_expand(g, 1u, rot, 1u, 0u, out) == 48u);
        /* every upright pixel appears at its rotated panel position */
        for (unsigned gy = 0; gy < 8u; gy++) {
            for (unsigned gx = 0; gx < 6u; gx++) {
                unsigned px, py;
                to_panel(lx + gx, ly + gy, rot, &px, &py);
                CHECK(px >= x && px < x + ww && py >= y && py < y + hh);
                CHECK(out[(py - y) * ww + (px - x)] == (uint16_t)upright(g, gx, gy));
            }
        }
        /* the whole portrait screen maps onto the panel exactly */
        vtext_window(0u, 0u, VTEXT_W, VTEXT_H, rot, &x, &y, &ww, &hh);
        CHECK(x == 0u && y == 0u && ww == VTEXT_PANEL_W && hh == VTEXT_PANEL_H);
    }
    /* scale 3: each upright pixel becomes a 3x3 block */
    uint16_t big[6 * 8 * 9];
    CHECK(vtext_expand(g, 3u, 0u, 1u, 0u, big) == 6u * 8u * 9u);
    CHECK(big[0] == 1u && big[2 * 18 + 2] == 1u && big[3 * 18 + 0] == 0u);   /* g[0] bit0 only */
    if (fails) return 1;
    printf("vtext ok\n");
    return 0;
}
