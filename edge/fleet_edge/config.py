"""ALL the settings for the edge laptop live here. Change values here, not in other files.

Units: metres, radians, seconds. World frame: origin at the bottom-left corner tag (CORNER_TAGS), x to the right, y up.
"""

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
FORKLIFT_TAGS = {13: "fl-01"}
HERO_FORKLIFT = "fl-01"       # the one Ring 1 drives
# Height of the forklift's tag above the table (metres). Camera-measured 271 mm; check with
# a ruler. Needed because a raised tag looks shifted away from the point under the camera.
FORKLIFT_TAG_HEIGHT_M = 0.271

# If the tag arrow points somewhere other than the forklift's front, fix it here (radians).
# Our tag 13 is mounted turned 90 degrees: it read 176 deg with the fork facing +y (90 deg).
TAG_HEADING_OFFSET = -1.5708

# Where the tag sits relative to the chassis centre (midpoint between the axles), in the
# forklift's own frame: + forward (toward the fork), + left. The pose reported is the
# chassis centre, so the controller steers the right point. Measure with a ruler, viewed
# from above. 0, 0 = the tag is straight above the chassis centre.
# Tag 13 is over the front-right corner of the 235 x 108 mm chassis (rough).
# Fine-tune: spin the forklift in place; with the right offsets the reported x, y barely moves.
TAG_OFFSET_FORWARD_M = 0.1175
TAG_OFFSET_LEFT_M = -0.054

# ---------------------------------------------------------------- Free-WiLi serial link
# "auto" finds the Free-WiLi OG main CPU by its USB name. Or set a path by hand
# (macOS: `ls /dev/tty.usb*`, Linux: `ls /dev/ttyACM*`); the OG shows TWO ports.
SERIAL_PORT = "auto"
SERIAL_BAUD = 115200
COMMAND_RATE_HZ = 20          # how often we send W lines
LOST_TAG_STOP_S = 0.3         # tag not seen for this long -> send S (stop). Must stop within 0.5 s.

# ---------------------------------------------------------------- driving
MAX_SPEED = 45                # wheel command, 0..100. 60 blurred the raised tag (seen 35% of frames) and overshot ~27 mm
# Right side is stronger: "straight" drives curved left ~16 deg/s at 50. Straight legs send
# right = left * RIGHT_TRIM (spins untouched). Tune with drive_lab: , and . keys, then i/k.
RIGHT_TRIM = 0.85
MIN_SPEED = 25                # below this the motors stall (deadband). Measure in step4.
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
TURN_MIN_SPEED = 35           # spin speed when nearly lined up. Raise if it stalls mid-turn.
SLOWDOWN_M = 0.08             # start easing off this far from the waypoint; no re-spins inside it
ARRIVE_SIDE_M = 0.05          # drawn level with the waypoint and this close sideways = reached
SKIP_NEAR_M = 0.08            # standing this close to the next waypoint (not yet driving) = reached
# The chassis spins about a point this far ahead of its centre (front-heavy: fork mast +
# battery). Measured with drive_lab: 98-145 mm over 7 spins; re-measure if weight moves.
PIVOT_FWD_M = 0.11
# Border: tag 13 must stay this many pixels inside the camera frame. The raised tag is
# ~115 px across (58 px half-width), so 60 px keeps all of it in view, only just. Raise it
# if the tag gets lost near the frame edge. Every move is checked against this before it
# starts (turn_drive.py).
TAG_BORDER_MARGIN_PX = 60
ALLOW_REVERSE = True          # target behind -> back up to it instead of turning round

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
# The right end is 0.36, not 0.40: facing right there, tag 13 (front-right corner) would
# sit outside the camera's view at its 271 mm height (the border, see below).
WAYPOINTS = [(0.13, 0.13), (0.36, 0.13)]
LAPS = 3

# ---------------------------------------------------------------- pose filter
POSE_ALPHA = 0.6              # 0..1. Higher = trust new measurement more (less lag, more jitter).
