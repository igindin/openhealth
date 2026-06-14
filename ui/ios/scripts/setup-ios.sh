#!/usr/bin/env bash
# OpenHealth iOS — build, sign and install on your iPhone (self-host via Claude Code).
#
# Prereqs:
#   - Xcode + xcodegen (`brew install xcodegen`)
#   - your Apple ID set as DEVELOPMENT_TEAM in project.yml
#   - one-time: accept the Apple Developer Program License Agreement at
#     developer.apple.com (device signing fails until you do)
#   - iPhone connected over USB and trusted
#
# Usage: ui/ios/scripts/setup-ios.sh   (or set OPENHEALTH_DEVICE_UDID=<udid>)
set -euo pipefail

IOS_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # ui/ios
cd "$IOS_DIR"

echo "==> xcodegen generate"
xcodegen generate

UDID="${OPENHEALTH_DEVICE_UDID:-}"
if [ -z "$UDID" ]; then
  UDID="$(xcrun devicectl list devices 2>/dev/null | awk '/available \(paired\)/ {print $(NF-2); exit}')"
fi
if [ -z "$UDID" ]; then
  echo "No paired device found. Connect + trust your iPhone, or set OPENHEALTH_DEVICE_UDID." >&2
  exit 1
fi
echo "==> device: $UDID"

echo "==> build (device, with provisioning updates)"
xcodebuild build -project OpenHealth.xcodeproj -scheme OpenHealth \
  -destination "platform=iOS,id=$UDID" -configuration Debug \
  -allowProvisioningUpdates -derivedDataPath build/dd

APP="build/dd/Build/Products/Debug-iphoneos/OpenHealth.app"
echo "==> install + launch"
xcrun devicectl device install app --device "$UDID" "$APP"
xcrun devicectl device process launch --device "$UDID" --terminate-existing org.openhealth.app

echo "==> done. On the phone: Sync tab -> Allow Apple Health -> Sync now."
echo "    Then run the Mac side: scripts/install-launchagent.sh"
