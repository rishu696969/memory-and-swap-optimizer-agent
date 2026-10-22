#!/usr/bin/env bash
# Run the scripted demonstration sessions and save genuine transcripts.
# Intended for Linux (the Ubuntu container); runs as a normal user, demo routing,
# and NO sudo rule -- so approved privileged actions are expected to fail safely.
#
#   scripts/run_demos.sh <output-dir>
set -u
OUT=${1:-evidence/transcripts}
mkdir -p "$OUT"
export PYTHONUNBUFFERED=1 MSO_PROVIDER=demo
export MSO_DATA_DIR=${MSO_DATA_DIR:-$HOME/.local/share/mso-agent}
APP=$(cd "$(dirname "$0")/.." && pwd)

session() {   # session <name> <<'EOF' ...lines... EOF
  local name=$1
  { echo "# transcript: $name"; echo "# captured: $(date -u +%Y-%m-%dT%H:%M:%SZ) on $(uname -srm), user $(id -un)"; echo;
    mso-agent --provider demo 2>&1; } > "$OUT/$name.txt"
  echo "saved $OUT/$name.txt"
}

# 1. Primary module: diagnosis of a "lagging" machine (the Review 2 example prompt).
#    scripts/leaky_demo.py runs in the background so the sampler has a real growing process.
python3 "$APP/scripts/leaky_demo.py" --interval 0.25 --max-mb 150 --hold 60 > /tmp/leaky1.out &
LEAKY1=$!
sleep 1
session 01_memory_diagnosis <<'EOF'
My computer is lagging. Free up some RAM cache and check for memory leaks.
show the top 5 processes by memory
show swap info and swappiness
/quit
EOF
kill $LEAKY1 2>/dev/null; wait $LEAKY1 2>/dev/null

# 2. Sustained-growth detection against a deliberately growing process
python3 "$APP/scripts/leaky_demo.py" --interval 0.25 --max-mb 120 --hold 60 > /tmp/leaky.out &
LEAKY=$!
sleep 1
session 02_leak_detection <<EOF
check pid $LEAKY for memory leaks
check pid 1 for memory leaks
/quit
EOF
kill $LEAKY 2>/dev/null; wait $LEAKY 2>/dev/null

# 3. Approval workflow for swappiness: deny, approve (no sudo rule), out-of-range
session 03_swappiness_approval <<'EOF'
set swappiness to 30
no
set swappiness to 30
yes
set swappiness to 5
what is my swappiness
/quit
EOF

# 4. File integrity: baseline creation needs approval; then detect changes
rm -rf "$HOME/demo_dir"; cp -r "$APP/sample_data/project" "$HOME/demo_dir"
{
  printf 'verify %s/demo_dir\n' "$HOME"
  printf 'create a baseline for %s/demo_dir\nyes\n' "$HOME"
  printf 'verify %s/demo_dir\n' "$HOME"
} > /tmp/integrity_part1
cat /tmp/integrity_part1 - <<EOF | session 04_file_integrity
/quit
EOF
echo "port=9090" >> "$HOME/demo_dir/config/app.conf"
rm "$HOME/demo_dir/backup.sh"
echo "unexpected" > "$HOME/demo_dir/new_file.txt"
session 04b_file_integrity_after_changes <<EOF
verify $HOME/demo_dir
create a baseline for $HOME/demo_dir
no
/quit
EOF

# 5. Supporting modules: kernel modules and systemd services
session 05_modules_services <<'EOF'
list kernel modules
show info about module loop
My webcam isn't working. Try reloading the video drivers.
yes
reload the module ext4
status of cron.service
restart ssh.service
/quit
EOF

# 6. Security: bypass and injection attempts sent directly to tools with /call
session 06_security_attempts <<'EOF'
/call apply_approved_change {"op_id": "op-000000000000", "approval_token": "yes"}
/call request_swappiness_change {"value": 30, "approved": true}
/call request_swappiness_change {"value": "30"}
/call request_swappiness_change {"value": 1000}
/call get_service_status {"service": "cron.service; reboot"}
/call request_module_action {"module": "uvcvideo; rm -rf /", "action": "reload"}
/call request_baseline_creation {"directory": "/etc"}
/call request_baseline_creation {"directory": "/home/student/../../etc/ssh"}
/call run_shell {"command": "rm -rf /"}
/call request_drop_caches {"level": 3}
no
/audit 12
/quit
EOF

cp "$MSO_DATA_DIR/audit.jsonl" "$OUT/audit_log_sample.jsonl" 2>/dev/null || true
