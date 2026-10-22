#!/usr/bin/env bash
# Write docs/project_tree.txt (used in the report appendix).
cd "$(dirname "$0")/.."
find . -path ./.venv -prune -o -path ./.git -prune -o -name __pycache__ -prune -o -name .pytest_cache -prune \
  -o -name '*.egg-info' -prune -o -name dist -prune -o -name '.DS_Store' -prune -o -path ./report/assets -prune \
  -o -print | sed 's|^\./||' | grep -v '^\.$' | sort | awk -F/ '{ indent=""; for(i=1;i<NF;i++) indent=indent"    "; print indent $NF (system("test -d \"" $0 "\"")==0 ? "/" : "") }' > docs/project_tree.txt
wc -l docs/project_tree.txt
