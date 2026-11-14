"""ALL the settings for the edge laptop live here. Change values here, not in other files.

Units: metres, radians, seconds. World frame: origin at corner tag 0, x to the right, y up.
"""

# ---------------------------------------------------------------- camera
CAMERA_INDEX = 0              # 0 = first webcam. Try 1 if you get your laptop's built-in camera.
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720
CALIBRATION_FILE = "edge/calibration/camera.npz"   # written by step2_calibrate

# ---------------------------------------------------------------- AprilTags (family tag36h11)
TAG_FAMILY = "tag36h11"
TAG_SIZE_M = 0.06             # printed black-square edge length. Measure it!

# The 4 corner tags pin the world frame. Put the CENTRE of each tag at these spots (metres).
CORNER_TAGS = {
    0: (0.00, 0.00),   # bottom-left
    1: (1.50, 0.00),   # bottom-right
    2: (1.50, 1.00),   # top-right
    3: (0.00, 1.00),   # top-left
}

# Tag on each forklift's roof -> forklift ID used everywhere else.
FORKLIFT_TAGS = {10: "fl-01", 11: "fl-02"}
HERO_FORKLIFT = "fl-01"       # the one Ring 1 drives

# If the tag arrow points somewhere other than the forklift's front, fix it here (radians).
TAG_HEADING_OFFSET = 0.0

# ---------------------------------------------------------------- Free-WiLi serial link
SERIAL_PORT = "/dev/tty.usbmodem1101"   # run `ls /dev/tty.usb*` with the Free-WiLi plugged in
SERIAL_BAUD = 115200
COMMAND_RATE_HZ = 20          # how often we send W lines
LOST_TAG_STOP_S = 0.3         # tag not seen for this long -> send S (stop). Must stop within 0.5 s.

# ---------------------------------------------------------------- driving
MAX_SPEED = 60                # wheel command, 0..100. Start slow!
MIN_SPEED = 25                # below this the motors stall (deadband). Measure in step4.
LOOKAHEAD_M = 0.08            # pure pursuit look-ahead. Bigger = smoother, cuts corners more.
ARRIVE_RADIUS_M = 0.03        # "reached the waypoint" distance
WHEEL_BASE_M = 0.12           # distance between left and right wheels
TURN_IN_PLACE_RAD = 1.2       # if target is more than this far off-heading, spin first

# Ring 1 loop. Must sit inside the corner tags.
WAYPOINTS = [(0.40, 0.30), (1.10, 0.30), (1.10, 0.70), (0.40, 0.70)]
LAPS = 3

# ---------------------------------------------------------------- pose filter
POSE_ALPHA = 0.6              # 0..1. Higher = trust new measurement more (less lag, more jitter).
