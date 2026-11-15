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

# The 4 corner tags pin the world frame: origin at the BOTTOM-LEFT tag's centre, x to the
# right, y up (as seen in the camera image). Tag CENTRES, in metres, measured with a ruler
# (2026-11-14): bottom 527 mm, right side 406 mm. The left side is assumed equal to the
# right; if the tags aren't a true rectangle, measure it and put the real numbers here.
CORNER_TAGS = {
    11: (0.000, 0.000),   # bottom-left (origin)
    9:  (0.527, 0.000),   # bottom-right
    5:  (0.527, 0.406),   # top-right
    4:  (0.000, 0.406),   # top-left
}

# Tag on each forklift's roof -> forklift ID used everywhere else.
FORKLIFT_TAGS = {13: "fl-01"}
HERO_FORKLIFT = "fl-01"       # the one Ring 1 drives

# If the tag arrow points somewhere other than the forklift's front, fix it here (radians).
TAG_HEADING_OFFSET = 0.0

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
