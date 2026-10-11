// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "AIGroupCore", platforms: [.iOS(.v17), .macOS(.v13)], products: [.library(name: "AIGroupCore", targets: ["AIGroupCore"])], targets: [.target(name: "AIGroupCore", path: "AIGroup/Core"), .testTarget(name: "AIGroupCoreTests", dependencies: ["AIGroupCore"], path: "Tests")])
