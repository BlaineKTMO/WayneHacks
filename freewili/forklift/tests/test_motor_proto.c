/* Host test for main/motor_proto.c. Built and run by test_firmware_logic.py. */
#include <stdio.h>
#include <string.h>

#include "../main/motor_proto.h"
#include "../main/byte_ring.h"
#include "../main/rf_proto.h"
#include "../main/rf_sched.h"

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

static void test_byte_ring(void) {
    byte_ring_t r;
    byte_ring_init(&r);
    uint8_t b = 7;
    CHECK(!byte_ring_pop(&r, &b) && byte_ring_count(&r) == 0);
    CHECK(byte_ring_push(&r, 'W') && byte_ring_push(&r, ',') && byte_ring_count(&r) == 2);
    CHECK(byte_ring_pop(&r, &b) && b == 'W' && byte_ring_pop(&r, &b) && b == ',');
    /* fills to SIZE-1, then refuses; wraps around correctly */
    int pushed = 0;
    while (byte_ring_push(&r, (uint8_t)pushed)) pushed++;
    CHECK(pushed == (int)BYTE_RING_SIZE - 1 && byte_ring_free(&r) == 0);
    int ok = 1;
    for (int i = 0; i < pushed; i++) ok &= byte_ring_pop(&r, &b) && b == (uint8_t)i;
    CHECK(ok && byte_ring_count(&r) == 0);
    /* all-or-nothing: a telemetry line is never cut in half */
    const uint8_t line[] = "T,IDLE,0.00\n";
    for (int i = 0; i < (int)BYTE_RING_SIZE - 5; i++) (void)byte_ring_push(&r, 'x');
    CHECK(!byte_ring_push_all(&r, line, sizeof line - 1) && byte_ring_free(&r) == 4);
    byte_ring_init(&r);
    CHECK(byte_ring_push_all(&r, line, sizeof line - 1) && byte_ring_count(&r) == sizeof line - 1);
}

static void test_rf_addr(void) {
    uint8_t id = 0, p[RF_PAYLOAD_LEN];
    CHECK(strcmp(rf_split_addr("2:W,45,38", &id), "W,45,38") == 0 && id == 2);
    CHECK(strcmp(rf_split_addr("W,45,38", &id), "W,45,38") == 0 && id == RF_DEFAULT_ID);
    CHECK(strcmp(rf_split_addr("0:W,1,1", &id), "0:W,1,1") == 0 && id == RF_DEFAULT_ID);
    CHECK(strcmp(rf_split_addr("S,x", &id), "S,x") == 0 && id == 1);
    rf_pack(p, RF_KIND_TELEM, 2, 0, "T,IDLE,0.00");
    CHECK(rf_packet_id(p, RF_PAYLOAD_LEN) == 2);
    CHECK(rf_packet_id(p, RF_PAYLOAD_LEN - 1) == 0);
    p[0] = 0;
    CHECK(rf_packet_id(p, RF_PAYLOAD_LEN) == 0);
}

static void test_rf_sched(void) {
    rf_sched_t s;
    rf_out_t o;
    bool poll = false;
    rf_sched_init(&s);
    CHECK(!rf_sched_next(&s, 0, &o, &poll));
    /* first packet to each forklift is a poll; while awaiting the reply nothing goes */
    CHECK(rf_sched_push(&s, 1, "W,10,10") && rf_sched_push(&s, 2, "W,20,20"));
    CHECK(rf_sched_next(&s, 0, &o, &poll) && o.id == 1 && poll);
    CHECK(!rf_sched_next(&s, 5, &o, &poll));
    rf_sched_heard(&s, 2);                         /* someone else: still waiting */
    CHECK(!rf_sched_next(&s, 6, &o, &poll));
    rf_sched_heard(&s, 1);                         /* the answer: air is free */
    CHECK(rf_sched_next(&s, 7, &o, &poll) && o.id == 2 && poll);
    /* no answer at all: the slot times out */
    CHECK(rf_sched_push(&s, 1, "W,11,11"));
    CHECK(!rf_sched_next(&s, 7 + RF_SLOT_MS - 1, &o, &poll));
    CHECK(rf_sched_next(&s, 7 + RF_SLOT_MS, &o, &poll) && o.id == 1 && !poll);   /* polled < 200 ms ago */
    /* back to back between polls */
    CHECK(rf_sched_push(&s, 1, "W,12,12") && rf_sched_push(&s, 2, "W,22,22"));
    CHECK(rf_sched_next(&s, 40, &o, &poll) && !poll);
    CHECK(rf_sched_next(&s, 40, &o, &poll) && !poll);
    /* RF_POLL_MS later it polls again */
    CHECK(rf_sched_push(&s, 1, "W,13,13"));
    CHECK(rf_sched_next(&s, RF_POLL_MS, &o, &poll) && poll);
    rf_sched_heard(&s, 1);
    /* newer W replaces the older one for the same forklift; S is kept */
    rf_sched_init(&s);
    CHECK(rf_sched_push(&s, 1, "W,1,1") && rf_sched_push(&s, 1, "S,stop") && rf_sched_push(&s, 1, "W,2,2"));
    CHECK(s.count == 2 && strcmp(s.q[0].text, "S,stop") == 0 && strcmp(s.q[1].text, "W,2,2") == 0);
    /* full queue: oldest non-stop makes room; all stops: refused */
    rf_sched_init(&s);
    for (unsigned i = 0; i < RF_QUEUE_LEN; i++) CHECK(rf_sched_push(&s, (uint8_t)(1 + i % RF_MAX_ID), "F,10,100"));
    CHECK(rf_sched_push(&s, 1, "S,x") && s.count == RF_QUEUE_LEN && strcmp(s.q[RF_QUEUE_LEN - 1].text, "S,x") == 0);
    rf_sched_init(&s);
    for (unsigned i = 0; i < RF_QUEUE_LEN; i++) CHECK(rf_sched_push(&s, 1, "S,x"));
    CHECK(!rf_sched_push(&s, 1, "W,1,1"));
    CHECK(!rf_sched_push(&s, 0, "W,1,1") && !rf_sched_push(&s, RF_MAX_ID + 1, "W,1,1"));
}

static void test_rf_proto(void) {
    uint8_t p[RF_PAYLOAD_LEN];
    char line[RF_TEXT_MAX + 1];
    uint8_t seq = 0;
    CHECK(rf_pack(p, RF_KIND_CMD, RF_FORKLIFT_ID, 7, "W,45,38\n") == RF_PAYLOAD_LEN);
    CHECK(p[0] == RF_MAGIC && p[1] == RF_KIND_CMD && p[2] == RF_FORKLIFT_ID && p[3] == 7);
    CHECK(rf_unpack(p, RF_PAYLOAD_LEN, RF_KIND_CMD, RF_FORKLIFT_ID, line, &seq) &&
          strcmp(line, "W,45,38") == 0 && seq == 7);
    /* the parser on the other end accepts what comes out */
    cmd_t c = proto_parse_line(line);
    CHECK(c.kind == CMD_WHEELS && c.left == 45 && c.right == 38);
    /* wrong kind / id / length / magic are all refused */
    CHECK(!rf_unpack(p, RF_PAYLOAD_LEN, RF_KIND_TELEM, RF_FORKLIFT_ID, line, NULL));
    CHECK(!rf_unpack(p, RF_PAYLOAD_LEN, RF_KIND_CMD, 2, line, NULL));
    CHECK(!rf_unpack(p, RF_PAYLOAD_LEN - 1, RF_KIND_CMD, RF_FORKLIFT_ID, line, NULL));
    p[0] ^= 0xFF;
    CHECK(!rf_unpack(p, RF_PAYLOAD_LEN, RF_KIND_CMD, RF_FORKLIFT_ID, line, NULL));
    /* non-printable text refused */
    rf_pack(p, RF_KIND_CMD, RF_FORKLIFT_ID, 0, "W,1,1");
    p[RF_HEADER_LEN + 1] = 0x07;
    CHECK(!rf_unpack(p, RF_PAYLOAD_LEN, RF_KIND_CMD, RF_FORKLIFT_ID, line, NULL));
    /* long W refused (never half a command); long S truncated (a stop always goes) */
    CHECK(rf_pack(p, RF_KIND_CMD, 1, 0, "W,100,100,this,is,way,too,long,for,a,packet") == 0);
    CHECK(rf_pack(p, RF_KIND_CMD, 1, 0, "S,a_very_long_stop_reason_that_goes_on_and_on") == RF_PAYLOAD_LEN);
    CHECK(rf_unpack(p, RF_PAYLOAD_LEN, RF_KIND_CMD, 1, line, NULL) && line[0] == 'S' &&
          strlen(line) == RF_TEXT_MAX && proto_parse_line(line).kind == CMD_STOP);
    CHECK(rf_pack(p, RF_KIND_CMD, 1, 0, "\n") == 0);
    /* every telemetry line fits */
    char t[32];
    proto_format_telemetry(t, sizeof t, FL_STOPPED, 12.34f);
    CHECK(rf_pack(p, RF_KIND_TELEM, 1, 0, t) == RF_PAYLOAD_LEN);
}

int main(void) {
    test_rf_proto();
    test_rf_addr();
    test_rf_sched();
    test_byte_ring();
    test_parse();
    test_drive_channel();
    test_fork_channel();
    test_telemetry();
    test_line_rx();
    if (failures) { printf("%d failure(s)\n", failures); return 1; }
    printf("motor_proto: all passed\n");
    return 0;
}
