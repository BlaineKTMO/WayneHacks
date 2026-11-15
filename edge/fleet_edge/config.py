"""ALL the settings for the edge laptop live here. Change values here, not in other files.

Units: metres, radians, seconds. World frame: origin at the bottom-left corner tag (CORNER_TAGS), x to the right, y up.
"""

import os

# ---------------------------------------------------------------- camera
CAMERA_INDEX = 2              # eMeet C950 (/dev/video2). 0 is the laptop's built-in camera.
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720
# Fixed short exposure. Auto picked ~15.7 ms, which blurred the raised tag on a moving
# forklift: seen in 61% of frames while driving at 45. At 5 ms it was seen in 100%, and
# detection got faster (45 -> 27 ms/frame). Gain brightens the darker image back up.
# Units are the camera's (100 us steps). None = leave the camera on auto exposure.
CAMERA_EXPOSURE = 50          # 5 ms
CAMERA_GAIN = 100
CALIBRATION_FILE = "edge/calibration/camera.npz"   # written by step2_calibrate / calib_auto
# Last table map (built whenever all 4 corner tags are visible). Loaded at startup, so the
# forklift or its wires covering a corner tag doesn't stop localization.
TABLE_MAP_FILE = "edge/calibration/table_map.npz"
# Chessboard INNER corners (columns, rows) = squares - 1. Ours: 11x8 squares -> 10x7.
CHESSBOARD_INNER = (10, 7)

# ---------------------------------------------------------------- AprilTags (family tag36h11)
TAG_FAMILY = "tag36h11"
TAG_SIZE_M = 0.0414           # printed black-square edge length (measured 41.4 mm)

# The 4 corner tags pin the world frame: origin at the BOTTOM-LEFT tag's centre, x toward
# the bottom-right tag, y up (as seen in the camera image). Tag CENTRES, in metres.
# The tags are NOT a rectangle (tag 4 sits left of tag 11). Positions measured by the
# calibrated camera (2026-11-14), scaled so the bottom edge matches the ruler's 508 mm.
# Camera vs ruler: bottom 506/508, right 372/376, left 336/345 mm.
CORNER_TAGS = {
    11: (0.0000, 0.0000),   # bottom-left (origin)
    9:  (0.5080, 0.0000),   # bottom-right
    5:  (0.5086, 0.3732),   # top-right
    4:  (-0.0313, 0.3357),  # top-left
}

# Tag on each forklift's roof -> forklift ID used everywhere else.
FORKLIFT_TAGS = {13: "fl-01", 16: "fl-01"}
# fl-02 (no fork, Nov 15): tag 8. Its TAG_MOUNTS entry below is a PLACEHOLDER (deck
# height, over the turning point, "up" = forward) until calibrated with:
#   uv run python -m fleet_edge.calibrate_forklift fl-02 8
FORKLIFT_TAGS[8] = "fl-02"

# Things on the floor (Nov 15). Cargo = items a forklift with forks (fl-01) picks up: it
# drives up facing the item, lowers the forks, creeps in, raises them and carries the item
# to a dock. Docks = where cargo is delivered; their graph nodes follow these tags.
CARGO_TAGS = {3: "cargo-3", 10: "cargo-10"}
DOCK_TAGS = {0: "dock_A", 17: "dock_B"}
HERO_FORKLIFT = "fl-01"       # the one Ring 1 drives
# Height of the forklift's tag above the table (metres). Needed because a raised tag looks
# shifted away from the point under the camera. Tag 13 moved from the fork mast (271 mm)
# down onto the chassis deck: camera-measured 78 mm (from its apparent size). Lower =
# the camera tracks a 2.3x bigger area, and spins swing the tag less.
FORKLIFT_TAG_HEIGHT_M = 0.078

# If the tag arrow points somewhere other than the forklift's front, fix it here (radians).
# Our tag 13 is mounted turned 90 degrees: it read 176 deg with the fork facing +y (90 deg).
TAG_HEADING_OFFSET = -1.5708

# Where the tag sits relative to the chassis centre (midpoint between the axles), in the
# forklift's own frame: + forward (toward the fork), + left. The pose reported is the
# chassis centre, so the controller steers the right point. Measure with a ruler, viewed
# from above. 0, 0 = the tag is straight above the chassis centre.
# Tag 13 is over the front-right corner of the 235 x 108 mm chassis (rough).
# Fine-tune: spin the forklift in place; with the right offsets the reported x, y barely moves.
# Deck mount (Nov 15): the reported pose is the chassis's measured TURNING POINT, so spins
# are in place. Spin tests put it 74-96 mm forward and 59-85 mm left of the tag, i.e. the
# tag is 85 mm behind and 72 mm right of it.
# Fork motor added (Nov 15): the turning point moved LEFT. Four ~45 deg left spins turned
# about (fwd, left) +7/+36, -7/+26, -18/+10, -16/+12 mm of the old point (it wanders with
# heading; the old point swept 20-29 mm per spin and tripped the drift guard). Shifted by
# the mean, 21 mm left; SPIN_DRIFT_M widened for the remaining +-2 cm wander.
TAG_OFFSET_FORWARD_M = -0.085
TAG_OFFSET_LEFT_M = -0.093

# Every tag a forklift carries: tag id -> where it sits on the chassis. height above the
# table (m); fwd / left = the tag centre relative to the turning point (the reported pose),
# chassis frame, + forward / + left; heading = added to the tag's own "up" direction to get
# the forklift's heading. With two tags in view the heading comes from the line between
# them (wider baseline, steadier); with one, from that tag alone.
# Measure a new tag with:  uv run python -m fleet_edge.measure_tag <id>   (tag 13 in view)
TAG_MOUNTS = {
    13: dict(height=FORKLIFT_TAG_HEIGHT_M, fwd=TAG_OFFSET_FORWARD_M, left=TAG_OFFSET_LEFT_M,
             heading=TAG_HEADING_OFFSET),
    # 16: raised at the front of the chassis. measure_tag 16 (Nov 15, fresh table map,
    # 77 frames, height spread 0.8 mm).
    16: dict(height=0.198, fwd=0.046, left=-0.002, heading=-1.6250),   # left: -21 mm pivot shift
    8: dict(height=0.080, fwd=0.0, left=0.0, heading=0.0),   # fl-02 PLACEHOLDER: calibrate_forklift
}

# ---------------------------------------------------------------- Free-WiLi serial link
# "auto" finds the Free-WiLi OG main CPU by its USB name. Or set a path by hand
# (macOS: `ls /dev/tty.usb*`, Linux: `ls /dev/ttyACM*`); the OG shows TWO ports.
# "auto" = find the OG on USB. Over WiFi: set FORKLIFT_PORT=socket://forklift-01.local:5000
# (or the IP shown on the OG's screen) instead of editing this.
SERIAL_PORT = os.environ.get("FORKLIFT_PORT", "auto")
SERIAL_BAUD = 115200
COMMAND_RATE_HZ = 20          # how often we send W lines

# bridge.py (Ring 2+): which forklift is wired to which Free-WiLi. Only these are driven.
# "auto" = the forklift's own USB if plugged in, else the radio base; "radio"; or a port/URL.
FORKLIFT_LINKS = {"fl-01": SERIAL_PORT, "fl-02": os.environ.get("FL02_PORT", "auto")}
FORKLIFT_RADIO_IDS = {"fl-01": 1, "fl-02": 2}          # firmware RF_FORKLIFT_ID
FORKLIFT_USB_PRODUCTS = {"fl-01": "FWOG main forklift", "fl-02": "FWOG main fl02"}
FORKLIFT_HAS_FORK = {"fl-01": True, "fl-02": False}
# Add the second one when it exists, e.g.  "fl-02": "/dev/tty.usbmodem2101"
SIM_START = {"fl-01": "dock_A", "fl-02": "dock_B"}   # --sim only: where simulated forklifts begin
LOST_TAG_STOP_S = 0.3         # tag not seen for this long -> send S (stop). Must stop within 0.5 s.

# ---------------------------------------------------------------- driving
MAX_SPEED = 45                # wheel command, 0..100. 60 blurred the raised tag (seen 35% of frames) and overshot ~27 mm
# Right side is stronger: "straight" drives curved left ~16 deg/s at 50. Straight legs send
# right = left * RIGHT_TRIM (spins untouched). Tune with drive_lab: , and . keys, then i/k.
RIGHT_TRIM = 0.85
MIN_SPEED = 30                # deadband, measured on the table: 25 doesn't move at all, 30 moves 58 mm in 0.6 s
LOOKAHEAD_M = 0.08            # pure pursuit look-ahead. Bigger = smoother, cuts corners more.
ARRIVE_RADIUS_M = 0.03        # "reached the waypoint" distance
WHEEL_BASE_M = 0.10           # left-to-right wheel distance (chassis is 108 mm wide; measure wheel centres)
TURN_IN_PLACE_RAD = 1.2       # if target is more than this far off-heading, spin first
# 4-wheel skid steer turns less sharply than a 2-wheel robot: if turns come out short,
# raise WHEEL_BASE_M (an "effective" track width, often 1.5-2x the measured one).

# Turn-then-drive (turn_drive.py, what Ring 1 uses): spin in place, then drive straight.
ALIGN_TOL_RAD = 0.14          # ~8 deg: stop spinning and start driving once this close
REALIGN_RAD = 0.35            # ~20 deg: drifted this far on a straight leg -> stop and re-spin
TURN_SPEED = 50               # spin speed when far off-heading. Skid steer needs more push to turn.
TURN_MIN_SPEED = 35           # spin speed when nearly lined up. Spin deadband measured: 25 stalls, 30 turns
SLOWDOWN_M = 0.08             # start easing off this far from the waypoint
NO_REALIGN_M = 0.15           # no re-spins this close to a waypoint (a small sideways miss is fine)
ARRIVE_SIDE_M = 0.05          # drawn level with the waypoint and this close sideways = reached
SPIN_DRIFT_M = 0.06      # was 0.04: with the fork motor the turning centre wanders +-2 cm (Nov 15)
MAX_DRIFTS = 2                # more drift re-plans than this on one leg -> stop for good (DRIFT_STOP)
# Clockwise spins (right wheels reversing) slide sideways on this chassis instead of turning
# on the spot (real loops 2-5); anticlockwise ones stay put. Turn left only: a 90 deg right
# corner becomes 270 deg left. The full 360 deg sweep at each loop corner clears the pole.
SPIN_LEFT_ONLY = True
SKIP_NEAR_M = 0.08            # standing this close to the next waypoint (not yet driving) = reached
# The chassis spins about a point this far ahead of its centre (front-heavy: fork mast +
# battery). Measured with drive_lab: 98-145 mm over 7 spins; re-measure if weight moves.
PIVOT_FWD_M = 0.0             # the pose IS the turning point now (see TAG_OFFSET_*)
# Border: tag 13 must stay this many pixels inside the camera frame. The raised tag is
# ~115 px across (58 px half-width), so 60 px keeps all of it in view, only just. Raise it
# if the tag gets lost near the frame edge. Every move is checked against this before it
# starts (turn_drive.py).
TAG_BORDER_MARGIN_PX = 60
ALLOW_REVERSE = False         # target behind -> back up instead of turning. Off for the loop: its
                              # clearances were checked for forward driving only

# ---------------------------------------------------------------- fork
FORK_SPEED = 60               # fork motor command, 0..100. + = up.
FORK_STEP_MS = 300            # how long one key press in step4 runs the fork
FORK_LIFT_MS = 1500           # full lift / lower time for Pick/Drop. Measure it! Max 5000.

# Ring 1 route: a straight back-and-forth along the bottom of the table. Must sit inside
# the corner tags with room for the 235 x 108 mm chassis: ~13 cm clear of every tag so
# the forklift (and its turns) never covers one. A pole stands near the top edge at about
# (0.22, 0.38) m (re-measured after the tags moved), and its base plate makes it ~4 cm wide: keep the forklift's centre at
# least ~17 cm from it (13 cm turning reach + base + margin). A full loop doesn't fit
# beside it, so the route stays on the bottom lane.
# Waypoints are where the chassis's turning point goes (the reported pose; see TAG_OFFSET_*).
# Ring 1 loop: clockwise rectangle, driven forwards. The largest found by searching every
# rectangle against the camera border at the tag's height, the pole and the corner tags
# (worst case over every leg and corner turn, chassis ~28 x 17 cm incl. the fork tines):
# tag >= 32 mm inside the border, chassis >= 66 mm from the pole's centre, >= 29 mm from
# every corner tag. Re-run the search if the camera, tags or tag mount move.
WAYPOINTS = [(0.12, 0.20), (0.42, 0.20), (0.42, 0.14), (0.12, 0.14)]
# Earlier 2-point shuttle (with ALLOW_REVERSE): [(0.24, 0.13), (0.47, 0.13)]
LAPS = 3

# ---------------------------------------------------------------- pose filter
POSE_ALPHA = 0.6              # 0..1. Higher = trust new measurement more (less lag, more jitter).
