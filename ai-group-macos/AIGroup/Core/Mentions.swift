import Foundation
public struct MentionMatch: Equatable {
    public let range: NSRange
    public let query: String
}
public enum Mentions {
    public static func match(text: String, caret: Int) -> MentionMatch? {
        let ns = text as NSString
        guard caret >= 0, caret <= ns.length else { return nil }
        let prefix = ns.substring(to: caret) as NSString
        let at = prefix.range(of: "@", options: .backwards)
        guard at.location != NSNotFound else { return nil }
        if at.location > 0 {
            let previous = prefix.substring(with: NSRange(location: at.location-1, length: 1))
            guard previous.rangeOfCharacter(from: .whitespacesAndNewlines.union(.punctuationCharacters)) != nil, previous != "@" else { return nil }
        }
        let tail = prefix.substring(from: at.location+1)
        guard tail.count <= 60, tail.rangeOfCharacter(from: .whitespacesAndNewlines) == nil else { return nil }
        return MentionMatch(range: NSRange(location: at.location, length: caret-at.location), query: tail)
    }
    public static func insert(text: String, match: MentionMatch, name: String) -> (text: String, caret: Int) {
        let replacement = "@" + name + " "
        return ((text as NSString).replacingCharacters(in: match.range, with: replacement), match.range.location + (replacement as NSString).length)
    }
}
