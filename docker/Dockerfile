# Ubuntu 24.04 test/demo environment for the Memory and Swap Optimizer Agent.
# The agent runs as the unprivileged user "student"; there is NO sudo rule by
# default, so approved privileged commands fail safely with "a password is required".
# docker/sudoers-mso-example is copied in only by scripts/linux_live_check.sh.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv python3-pip sudo kmod procps systemd graphviz fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash student
WORKDIR /home/student/app
COPY --chown=student:student pyproject.toml README.md ./
COPY --chown=student:student src ./src
COPY --chown=student:student tests ./tests
COPY --chown=student:student scripts ./scripts
COPY --chown=student:student sample_data ./sample_data
COPY --chown=student:student docker ./docker
RUN chown student:student /home/student/app

USER student
RUN python3 -m venv /home/student/venv \
 && /home/student/venv/bin/pip install --no-cache-dir -e ".[dev]"
ENV PATH=/home/student/venv/bin:$PATH \
    MSO_ALLOWED_ROOTS=/home/student \
    MSO_PROVIDER=demo
CMD ["mso-agent"]
