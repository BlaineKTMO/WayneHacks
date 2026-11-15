#include "vtext.h"

void vtext_window(unsigned lx, unsigned ly, unsigned w, unsigned h, unsigned rot,
                  unsigned *x, unsigned *y, unsigned *ww, unsigned *hh) {
    if (rot == 90u) {            /* panel (px, py) = (ly, PANEL_H-1 - lx) */
        *x = ly; *y = VTEXT_PANEL_H - lx - w; *ww = h; *hh = w;
    } else if (rot == 270u) {    /* panel (px, py) = (PANEL_W-1 - ly, lx) */
        *x = VTEXT_PANEL_W - ly - h; *y = lx; *ww = h; *hh = w;
    } else {
        *x = lx; *y = ly; *ww = w; *hh = h;
    }
}

unsigned vtext_expand(const uint8_t cols[VTEXT_GLYPH_W], unsigned scale, unsigned rot,
                      uint16_t fg, uint16_t bg, uint16_t *out) {
    const unsigned w = VTEXT_GLYPH_W * scale, h = VTEXT_GLYPH_H * scale;
    unsigned ww, hh, x, y;
    vtext_window(0u, 0u, w, h, rot, &x, &y, &ww, &hh);
    unsigned n = 0;
    for (unsigned r = 0; r < hh; r++) {
        for (unsigned c = 0; c < ww; c++) {
            unsigned gx, gy;     /* pixel in the upright glyph */
            if (rot == 90u)       { gx = w - 1u - r; gy = c; }
            else if (rot == 270u) { gx = r;          gy = h - 1u - c; }
            else                  { gx = c;          gy = r; }
            const bool on = (cols[gx / scale] >> (gy / scale)) & 1u;
            out[n++] = on ? fg : bg;
        }
    }
    return n;
}

#ifndef HOST_TEST
#include "lcd/lcd_text.h"
#include "lcd/st7789.h"

void vtext_draw(unsigned lx, unsigned ly, const char *str, unsigned cols, unsigned scale,
                unsigned rot, uint16_t fg, uint16_t bg) {
    if (scale == 0u || scale > VTEXT_MAX_SCALE) return;
    if (rot != 90u && rot != 270u) {
        lcd_text_draw_padded((uint16_t)lx, (uint16_t)ly, str, cols, scale > 3u ? 3u : scale, fg, bg);
        return;
    }
    /* static: one glyph at scale 5 is 2.4 KB; drawn from the main loop only. */
    static uint16_t px[VTEXT_GLYPH_W * VTEXT_GLYPH_H * VTEXT_MAX_SCALE * VTEXT_MAX_SCALE];
    const unsigned adv = VTEXT_GLYPH_W * scale, h = VTEXT_GLYPH_H * scale;
    if (ly + h > VTEXT_H) return;
    bool ended = (str == NULL);
    for (unsigned i = 0; i < cols; i++) {
        const unsigned gx = lx + i * adv;
        if (gx + adv > VTEXT_W) break;           /* truncate, never wrap */
        if (!ended && str[i] == '\0') ended = true;
        unsigned x, y, ww, hh;
        vtext_window(gx, ly, adv, h, rot, &x, &y, &ww, &hh);
        const unsigned n = vtext_expand(lcd_font_glyph(ended ? ' ' : str[i]), scale, rot, fg, bg, px);
        st7789_set_window((uint16_t)x, (uint16_t)y, (uint16_t)ww, (uint16_t)hh);
        st7789_blit(px, n);
    }
}
#endif
