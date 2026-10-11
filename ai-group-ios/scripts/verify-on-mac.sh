#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin ]]; then
  echo "iPhone 界面验证需要 macOS 和完整 Xcode。" >&2
  exit 1
fi
if ! xcodebuild -version; then
  echo "请安装并打开 Xcode，完成首次组件安装，再重试。" >&2
  exit 1
fi
python3 scripts/test-core.py
xcodebuild -project AIGroup.xcodeproj -scheme AIGroup -configuration Debug -destination 'generic/platform=iOS Simulator' -derivedDataPath .build/xcode CODE_SIGNING_ALLOWED=NO build
