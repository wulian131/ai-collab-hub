#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/test-core.py
xcodebuild -project AIGroup.xcodeproj -scheme AIGroup -configuration Release -destination 'platform=macOS' -derivedDataPath .build/macos CODE_SIGNING_ALLOWED=NO build
