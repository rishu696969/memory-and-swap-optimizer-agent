"""A deliberately 'leaky' demo process for the sustained-growth detector.

It appends a 1 MiB block every --interval seconds (default 0.25 s) up to
--max-mb (default 120 MB), then holds the memory until --hold seconds pass.
The cap keeps the demonstration safe on a small VM.

    python scripts/leaky_demo.py &           # prints its PID
    mso-agent --provider demo --once "check pid <PID> for memory leaks"
"""

import argparse
import os
import time

parser = argparse.ArgumentParser()
parser.add_argument("--interval", type=float, default=0.25)
parser.add_argument("--max-mb", type=int, default=120)
parser.add_argument("--hold", type=float, default=120.0)
args = parser.parse_args()

print(f"leaky_demo pid={os.getpid()} growing 1 MiB every {args.interval}s up to {args.max_mb} MiB", flush=True)
blocks = []
start = time.monotonic()
while time.monotonic() - start < args.hold:
    if len(blocks) < args.max_mb:
        blocks.append(bytearray(os.urandom(1024 * 1024)))  # random bytes so pages are really resident
    time.sleep(args.interval)
