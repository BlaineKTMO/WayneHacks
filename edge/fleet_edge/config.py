"""ALL the settings for the edge laptop live here. Change values here, not in other files.

Units: metres, radians, seconds. World frame: origin at the bottom-left corner tag (CORNER_TAGS), x to the right, y up.
"""

# ---------------------------------------------------------------- camera
CAMERA_INDEX = 2              # eMeet C950 (/dev/video2). 0 is the laptop's built-in camera.
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720
CALIBRATION_FILE = "edge/calibration/camera.npz"   # written by step2_calibrate / calib_auto
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
TAG_OFFSET_FORWARD_M = 0.0
TAG_OFFSET_LEFT_M = 0.0

# ---------------------------------------------------------------- Free-WiLi serial link
# "auto" finds the Free-WiLi OG main CPU by its USB name. Or set a path by hand
# (macOS: `ls /dev/tty.usb*`, Linux: `ls /dev/ttyACM*`); the OG shows TWO ports.
SERIAL_PORT = "auto"
SERIAL_BAUD = 115200
COMMAND_RATE_HZ = 20          # how often we send W lines
LOST_TAG_STOP_S = 0.3         # tag not seen for this long -> send S (stop). Must stop within 0.5 s.

# ---------------------------------------------------------------- driving
MAX_SPEED = 60                # wheel command, 0..100. Start slow!
MIN_SPEED = 25                # below this the motors stall (deadband). Measure in step4.
LOOKAHEAD_M = 0.08            # pure pursuit look-ahead. Bigger = smoother, cuts corners more.
ARRIVE_RADIUS_M = 0.03        # "reached the waypoint" distance
WHEEL_BASE_M = 0.10           # left-to-right wheel distance (chassis is 108 mm wide; measure wheel centres)
TURN_IN_PLACE_RAD = 1.2       # if target is more than this far off-heading, spin first
# 4-wheel skid steer turns less sharply than a 2-wheel robot: if turns come out short,
# raise WHEEL_BASE_M (an "effective" track width, often 1.5-2x the measured one).

# ---------------------------------------------------------------- fork
FORK_SPEED = 60               # fork motor command, 0..100. + = up.
FORK_STEP_MS = 300            # how long one key press in step4 runs the fork
FORK_LIFT_MS = 1500           # full lift / lower time for Pick/Drop. Measure it! Max 5000.

# Ring 1 loop. Must sit inside the corner tags with room for the 235 x 108 mm chassis:
# ~13 cm clear of every tag so the forklift (and its turns) never covers one.
WAYPOINTS = [(0.15, 0.13), (0.38, 0.13), (0.38, 0.28), (0.15, 0.28)]
LAPS = 3

# ---------------------------------------------------------------- pose filter
POSE_ALPHA = 0.6              # 0..1. Higher = trust new measurement more (less lag, more jitter).
