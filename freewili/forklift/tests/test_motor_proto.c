/* Host test for main/motor_proto.c. Built and run by test_firmware_logic.py. */
#include <stdio.h>
#include <string.h>

#include "../main/motor_proto.h"

static int failures;
#define CHECK(cond) do { if (!(cond)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #cond); failures++; } } while (0)

static void test_parse(void) {
    cmd_t c = proto_parse_line("W,40,-25");
    CHECK(c.kind == CMD_WHEELS && c.left == 40 && c.right == -25);

    c = proto_parse_line("W,150,-300");            /* clamped, as freewili_link.clamp does */
    CHECK(c.kind == CMD_WHEELS && c.left == 100 && c.right == -100);

    c = proto_parse_line("W,0,0\r");
    CHECK(c.kind == CMD_WHEELS && c.left == 0 && c.right == 0);

    CHECK(proto_parse_line("S,lost_tag").kind == CMD_STOP);
    CHECK(proto_parse_line("S").kind == CMD_STOP);

    /* Malformed lines must be ignored, not half-applied. */
    CHECK(proto_parse_line("").kind == CMD_NONE);
    CHECK(proto_parse_line("W,40").kind == CMD_NONE);
    CHECK(proto_parse_line("W,40,").kind == CMD_NONE);
    CHECK(proto_parse_line("W,4x,10").kind == CMD_NONE);
    CHECK(proto_parse_line("W,,10").kind == CMD_NONE);
    CHECK(proto_parse_line("X,1,2").kind == CMD_NONE);
    CHECK(proto_parse_line("Stop").kind == CMD_NONE);
    CHECK(proto_parse_line("T,IDLE,7.40").kind == CMD_NONE);
    CHECK(proto_parse_line(NULL).kind == CMD_NONE);
}

static void test_l298n(void) {
    l298n_channel_t ch = l298n_channel(60);
    CHECK(ch.in1 && !ch.in2 && ch.duty_pct == 60);
    ch = l298n_channel(-35);
    CHECK(!ch.in1 && ch.in2 && ch.duty_pct == 35);
    ch = l298n_channel(0);                          /* brake: EN high, IN1 == IN2 */
    CHECK(!ch.in1 && !ch.in2 && ch.duty_pct == 100);
    ch = l298n_channel(500);
    CHECK(ch.duty_pct == 100 && ch.in1);
}

static void test_telemetry(void) {
    char buf[32];
    size_t n = proto_format_telemetry(buf, sizeof buf, FL_DRIVING, 7.4f);
    CHECK(strcmp(buf, "T,DRIVING,7.40\n") == 0 && n == strlen(buf));
    proto_format_telemetry(buf, sizeof buf, FL_STOPPED, 0.0f);
    CHECK(strcmp(buf, "T,STOPPED,0.00\n") == 0);
}

static void feed(line_rx_t *rx, const char *s, char out[][PROTO_LINE_MAX], int *n) {
    for (; *s; s++)
        if (line_rx_feed(rx, *s)) strcpy(out[(*n)++], rx->buf);
}

static void test_line_rx(void) {
    line_rx_t rx;
    line_rx_init(&rx);
    char lines[8][PROTO_LINE_MAX];
    int n = 0;
    feed(&rx, "W,10,", lines, &n);
    feed(&rx, "20\r\nS,x\n\n", lines, &n);              /* split across reads, CRLF, blank line */
    CHECK(n == 2 && strcmp(lines[0], "W,10,20") == 0 && strcmp(lines[1], "S,x") == 0);

    char longline[PROTO_LINE_MAX + 20];
    memset(longline, '9', sizeof longline);
    memcpy(longline, "W,1,", 4);
    longline[sizeof longline - 1] = '\0';
    n = 0;
    feed(&rx, longline, lines, &n);
    feed(&rx, "\nW,5,5\n", lines, &n);                  /* over-long dropped whole */
    CHECK(n == 1 && strcmp(lines[0], "W,5,5") == 0);
}

int main(void) {
    test_parse();
    test_l298n();
    test_telemetry();
    test_line_rx();
    if (failures) { printf("%d failure(s)\n", failures); return 1; }
    printf("motor_proto: all passed\n");
    return 0;
}
