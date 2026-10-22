#!/usr/bin/env bash
# LIVE check of the two memory-module privileged actions on a real Linux kernel.
# Must be started as root inside a DISPOSABLE privileged container (see
# scripts/collect_evidence.sh).  It installs the narrow sudoers example, then
# runs the agent as the normal user "student", approves the changes, and
# restores the original vm.swappiness afterwards.
set -eu
OUT=${1:-/tmp/live}
mkdir -p "$OUT"; chmod 777 "$OUT"
install -m 0440 /home/student/app/docker/sudoers-mso-example /etc/sudoers.d/mso-agent
visudo -cf /etc/sudoers.d/mso-agent

ORIG=$(cat /proc/sys/vm/swappiness)
echo "original vm.swappiness=$ORIG" | tee "$OUT/live_summary.txt"

runuser -u student -- env PATH=/home/student/venv/bin:/usr/bin:/bin PYTHONUNBUFFERED=1 MSO_PROVIDER=demo \
  MSO_ALLOWED_ROOTS=/home/student bash -c "
  { echo '# transcript: 07_live_privileged_swappiness (privileged container, narrow sudoers rule)';
    echo \"# captured: \$(date -u +%Y-%m-%dT%H:%M:%SZ) on \$(uname -srm), user \$(id -un)\"; echo;
    printf 'set swappiness to 35\nyes\nwhat is my swappiness\nset swappiness to $ORIG\nyes\nwhat is my swappiness\ndrop caches level 1\nyes\n/quit\n' | mso-agent --provider demo 2>&1; } > $OUT/07_live_privileged.txt
  # A value outside the sudoers rule must be refused by sudo even if the agent were bypassed:
  { echo '# direct sudo attempts outside the narrow rule (expected to fail)';
    sudo -n /usr/sbin/sysctl -w vm.swappiness=5 2>&1 || echo \"exit=\$?\";
    sudo -n /usr/sbin/sysctl -w vm.overcommit_memory=2 2>&1 || echo \"exit=\$?\";
    sudo -n /usr/bin/id 2>&1 || echo \"exit=\$?\"; } > $OUT/07b_sudo_scope_check.txt
"
FINAL=$(cat /proc/sys/vm/swappiness)
echo "final vm.swappiness=$FINAL (restored: $([ "$FINAL" = "$ORIG" ] && echo yes || echo NO))" | tee -a "$OUT/live_summary.txt"
[ "$FINAL" = "$ORIG" ] || sysctl -w vm.swappiness="$ORIG"
