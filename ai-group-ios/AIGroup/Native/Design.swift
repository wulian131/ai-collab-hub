import SwiftUI
import UIKit
struct RoomAccentKey: EnvironmentKey { static let defaultValue = AppPalette.color("mint") }
extension EnvironmentValues {
    var roomAccent: Color { get { self[RoomAccentKey.self] } set { self[RoomAccentKey.self] = newValue } }
}
enum AppPalette {
    static func color(_ choice: String) -> Color {
        Color(UIColor { traits in
            let dark = traits.userInterfaceStyle == .dark
            switch choice {
            case "blue": return dark ? UIColor(red: 0.60, green: 0.77, blue: 1, alpha: 1) : UIColor(red: 0.19, green: 0.43, blue: 0.71, alpha: 1)
            case "violet": return dark ? UIColor(red: 0.79, green: 0.71, blue: 0.95, alpha: 1) : UIColor(red: 0.46, green: 0.32, blue: 0.71, alpha: 1)
            default: return dark ? UIColor(red: 0.57, green: 0.84, blue: 0.69, alpha: 1) : UIColor(red: 0.14, green: 0.45, blue: 0.32, alpha: 1)
            }
        })
    }
}
@MainActor struct StatusPill: View {
    var text: String
    var color: Color = .secondary
    var body: some View { Text(text).font(.caption.weight(.medium)).padding(.horizontal, 10).padding(.vertical, 5).foregroundStyle(color).background(color.opacity(0.10), in: Capsule()) }
}
func timestamp(_ seconds: Double) -> String { Date(timeIntervalSince1970: seconds).formatted(date: .abbreviated, time: .shortened) }
@MainActor struct Avatar: View {
    let id: String
    let name: String
    var online: Bool? = nil
    var body: some View {
        let colors: [Color] = [.teal, .indigo, .brown, .blue, .purple]
        let index = id.utf8.reduce(0) { ($0 + Int($1)) % colors.count }
        ZStack(alignment: .bottomTrailing) {
            Text(String(name.prefix(2))).font(.system(size: 13, weight: .semibold)).foregroundStyle(.white)
                .frame(width: 38, height: 38).background(colors[index].gradient, in: RoundedRectangle(cornerRadius: 13))
            if let online { Circle().fill(online ? Color.green : Color.gray).frame(width: 9, height: 9).overlay(Circle().stroke(Color(.systemBackground), lineWidth: 2)).offset(x: 1, y: 1) }
        }.accessibilityLabel(name + (online == true ? "，在线" : ""))
    }
}
@MainActor struct EmptyState: View {
    @Environment(\.roomAccent) private var accent
    var title: String; var detail: String; var icon: String = "bubble.left.and.bubble.right"
    var body: some View { VStack(spacing: 16) { Image(systemName: icon).font(.system(size: 40)).foregroundStyle(accent.opacity(0.6)); Text(title).font(.headline); Text(detail).font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center) }.padding(30).frame(maxWidth: .infinity, maxHeight: .infinity) }
}
@MainActor struct MarkdownBody: View {
    let text: String
    var body: some View {
        let sections = text.components(separatedBy: "```")
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(sections.enumerated()), id: \.offset) { index, part in
                if index % 2 == 1 {
                    ScrollView(.horizontal) { Text(part.trimmingCharacters(in: .newlines)).font(.system(.caption, design: .monospaced)).textSelection(.enabled).padding(12) }.background(Color(.tertiarySystemFill), in: RoundedRectangle(cornerRadius: 9))
                } else if !part.isEmpty {
                    Text((try? AttributedString(markdown: part, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(part)).textSelection(.enabled)
                }
            }
        }.font(.body).frame(maxWidth: .infinity, alignment: .leading).environment(\.openURL, OpenURLAction { url in ["http", "https"].contains(url.scheme?.lowercased() ?? "") ? .systemAction : .discarded })
    }
}
