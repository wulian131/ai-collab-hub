import SwiftUI
import AppKit
@MainActor enum DesktopPasteboard {
    static func copy(_ text: String, expires: Date? = nil) {
        let board = NSPasteboard.general
        board.clearContents(); board.setString(text, forType: .string)
        if let expires {
            let change = board.changeCount
            Task { try? await Task.sleep(for: .seconds(max(0, expires.timeIntervalSinceNow)))
                if board.changeCount == change { board.clearContents() }
            }
        }
    }
}
extension Notification.Name {
    static let desktopNewConversation = Notification.Name("desktopNewConversation")
    static let desktopSearch = Notification.Name("desktopSearch")
    static let desktopRefresh = Notification.Name("desktopRefresh")
}
