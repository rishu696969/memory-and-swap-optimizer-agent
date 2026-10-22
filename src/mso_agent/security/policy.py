"""Fixed security policy: bounds, name patterns and limits in one place.

Keeping the numbers here makes them easy to show in a viva and easy to test.
"""

import re

# --- Numeric bounds -------------------------------------------------------
SWAPPINESS_MIN = 10          # values below 10 almost disable swapping of anon pages
SWAPPINESS_MAX = 100         # we do not allow the 101-200 range added in Linux 5.8
DROP_CACHES_LEVELS = (1, 2, 3)

TOP_PROCESSES_MAX = 25
SAMPLES_MIN, SAMPLES_MAX = 3, 30
INTERVAL_MIN_S, INTERVAL_MAX_S = 0.2, 10.0
SAMPLING_BUDGET_S = 120.0    # samples * interval may not exceed this
GROWTH_THRESHOLD_MIN_MB, GROWTH_THRESHOLD_MAX_MB = 1, 4096
WINDOW_MIN_S, WINDOW_MAX_S = 1, 10

# --- Command execution limits --------------------------------------------
DEFAULT_TIMEOUT_S = 15.0
MAX_TIMEOUT_S = 60.0
DEFAULT_OUTPUT_LIMIT = 64 * 1024     # bytes kept per stream
MAX_ARG_LEN = 256
MAX_ARGS = 16

# --- File integrity limits ------------------------------------------------
MAX_PATH_LEN = 4096
MAX_FILES = 20_000
MAX_FILE_BYTES = 1024 * 1024 * 1024   # files larger than 1 GiB are listed but not hashed
HASH_CHUNK = 1024 * 1024
# Never hash pseudo filesystems even if an allowed root contains them.
FORBIDDEN_PATH_PREFIXES = ("/proc", "/sys", "/dev", "/run")

# --- Names ------------------------------------------------------------------
# Kernel module names are [a-z0-9_]; modprobe treats '-' and '_' the same, we normalise to '_'.
MODULE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,55}$")
# systemd unit names: letters, digits, ':', '_', '.', '@', '-'; must not start with '-' (option injection).
SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.@\-]{0,120}\.service$")
MODULE_ACTIONS = ("load", "unload", "reload")
SERVICE_ACTIONS = ("restart", "enable")

# Tools that the host (REPL) may call but which are never offered to the model.
HOST_ONLY_TOOLS = frozenset({"apply_approved_change", "cancel_pending_change"})
