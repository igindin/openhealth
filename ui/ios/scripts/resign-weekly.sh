#!/usr/bin/env bash
# Free Apple ID development profiles expire after 7 days. Re-run this weekly to
# re-sign and re-install the app (paid Developer Program profiles last ~1 year,
# so this is only needed on a free Apple ID). Just re-runs setup-ios.sh.
set -euo pipefail
exec "$(dirname "$0")/setup-ios.sh"
