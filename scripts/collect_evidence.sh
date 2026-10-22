#!/usr/bin/env bash
# Rebuild all evidence in ./evidence from scratch (needs Docker).
#   1. unit/integration tests on Ubuntu 24.04 as a normal user (+ JUnit XML)
#   2. scripted demo transcripts (demo routing, no sudo rule)
#   3. OPTIONAL live privileged check in a disposable privileged container
#      (pass --live; it changes vm.swappiness of the Docker VM and restores it)
set -eu
cd "$(dirname "$0")/.."
LIVE=${1:-}
IMG=mso-agent:test
docker build -q -f docker/Dockerfile -t $IMG . >/dev/null
mkdir -p evidence/test_results evidence/transcripts evidence/live_privileged
chmod 777 evidence/test_results evidence/transcripts evidence/live_privileged

docker run --rm -v "$PWD/evidence:/out" $IMG bash -c '
  { echo "# environment"; uname -a; grep PRETTY /etc/os-release; python3 --version; id;
    pip show mcp psutil pydantic | grep -E "^(Name|Version)"; } > /out/test_results/linux_environment.txt
  pytest -v -p no:cacheprovider --junitxml=/out/test_results/junit_linux.xml > /out/test_results/pytest_linux.txt 2>&1
  echo "pytest exit code: $?" >> /out/test_results/pytest_linux.txt'

docker run --rm -v "$PWD/evidence:/out" $IMG bash -c 'scripts/run_demos.sh /out/transcripts'

if [ "$LIVE" = "--live" ]; then
  docker run --rm --privileged --user root -v "$PWD/evidence:/out" $IMG \
    bash /home/student/app/scripts/linux_live_check.sh /out/live_privileged
fi
echo "evidence written to ./evidence"
