/* Text on a panel mounted on its side (portrait), for the forklifts' screens.
 *
 * The BSP's ST7789 driver is fixed at 320 x 240 landscape and lcd_text only draws
 * left-to-right, so this rotates each glyph itself and blits it into the matching
 * landscape window. Logical (portrait) coordinates: VTEXT_W x VTEXT_H, origin top-left
 * as you read the mounted screen.
 *
 *   rot  90: the panel's landscape top edge is on the reader's RIGHT
 *   rot 270: ... on the reader's LEFT   (flip FORKLIFT_SCREEN_ROTATION if text is upside down)
 *   rot   0: plain landscape (lcd_text)
 *
 * vtext_window / vtext_expand are pure (host-tested); vtext_draw talks to the panel. */
#ifndef FORKLIFT_VTEXT_H
#define FORKLIFT_VTEXT_H

#include <stdbool.h>
#include <stdint.h>

#define VTEXT_PANEL_W 320u
#define VTEXT_PANEL_H 240u
#define VTEXT_W VTEXT_PANEL_H     /* portrait width  */
#define VTEXT_H VTEXT_PANEL_W     /* portrait height */
#define VTEXT_GLYPH_W 6u
#define VTEXT_GLYPH_H 8u
#define VTEXT_MAX_SCALE 5u

/* Landscape window (x, y, w, h) for a logical w x h block at (lx, ly). */
void vtext_window(unsigned lx, unsigned ly, unsigned w, unsigned h, unsigned rot,
                  unsigned *x, unsigned *y, unsigned *ww, unsigned *hh);

/* One glyph (6 column bytes, bit N = row N, as lcd_font_glyph returns) at `scale`, rotated
 * into the window's row-major pixel order. Returns pixels written. */
unsigned vtext_expand(const uint8_t cols[VTEXT_GLYPH_W], unsigned scale, unsigned rot,
                      uint16_t fg, uint16_t bg, uint16_t *out);

#ifndef HOST_TEST
/* Draw `str` padded with spaces to `cols` characters (erases a longer old string). */
void vtext_draw(unsigned lx, unsigned ly, const char *str, unsigned cols, unsigned scale,
                unsigned rot, uint16_t fg, uint16_t bg);
#endif

#endif
