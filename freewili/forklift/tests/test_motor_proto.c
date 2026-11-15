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

    c = proto_parse_line("F,60,1500");
    CHECK(c.kind == CMD_FORK && c.fork == 60 && c.fork_ms == 1500);
    c = proto_parse_line("F,-200,99999");               /* speed clamped, time capped */
    CHECK(c.kind == CMD_FORK && c.fork == -100 && c.fork_ms == FORK_MAX_MS);
    c = proto_parse_line("F,0,0");
    CHECK(c.kind == CMD_FORK && c.fork == 0 && c.fork_ms == 0);
    CHECK(proto_parse_line("F,50,-1").kind == CMD_NONE);  /* negative time rejected */
    CHECK(proto_parse_line("F,50").kind == CMD_NONE);
    CHECK(proto_parse_line("F,up,100").kind == CMD_NONE);
}

static void test_drive_channel(void) {
    pwm_pair_t p = drive_channel(60);
    CHECK(p.a_pct == 60 && p.b_pct == 0);
    p = drive_channel(-35);
    CHECK(p.a_pct == 0 && p.b_pct == 35);
    p = drive_channel(0);                              /* brake: both IN low */
    CHECK(p.a_pct == 0 && p.b_pct == 0);
    p = drive_channel(500);
    CHECK(p.a_pct == 100 && p.b_pct == 0);
}

static void test_fork_channel(void) {
    fork_out_t f = fork_channel(70);                   /* up: A drives, B low */
    CHECK(f.a_pct == 70 && !f.b_high);
    f = fork_channel(-70);                             /* down: B high, A low 70% of the time */
    CHECK(f.a_pct == 30 && f.b_high);
    f = fork_channel(-100);                            /* full down: A never high */
    CHECK(f.a_pct == 0 && f.b_high);
    f = fork_channel(0);                               /* brake */
    CHECK(f.a_pct == 0 && !f.b_high);
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
    test_drive_channel();
    test_fork_channel();
    test_telemetry();
    test_line_rx();
    if (failures) { printf("%d failure(s)\n", failures); return 1; }
    printf("motor_proto: all passed\n");
    return 0;
}
