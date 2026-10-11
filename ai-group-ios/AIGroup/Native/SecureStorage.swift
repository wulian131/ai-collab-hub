import Foundation
import Security
import CryptoKit

struct SavedSession: Codable { let address: String; let token: String }
enum KeychainStore {
    private static let service = "org.example.aicollabhub.ios.session.v1"
    private static let account = "current"
    static func read() throws -> SavedSession? {
        guard UserDefaults.standard.bool(forKey: "aiCollabHubPublicSessionSaved") else { return nil }
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account, kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else { throw ClientError.validation("无法读取安全登录信息，请解锁设备后重试") }
        return try JSONDecoder().decode(SavedSession.self, from: data)
    }
    static func write(_ value: SavedSession) throws {
        let data = try JSONEncoder().encode(value)
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
        let attributes: [String: Any] = [kSecValueData as String: data, kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            let added = SecItemAdd(query.merging(attributes) { _, new in new } as CFDictionary, nil)
            guard added == errSecSuccess else { throw ClientError.validation("安全保存登录失败，未记住凭证") }
        } else if status != errSecSuccess { throw ClientError.validation("安全保存登录失败，未记住凭证") }
        UserDefaults.standard.set(true, forKey: "aiCollabHubPublicSessionSaved")
    }
    static func clear() { UserDefaults.standard.set(false, forKey: "aiCollabHubPublicSessionSaved"); SecItemDelete([kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account] as CFDictionary) }
}
struct LocalSnapshot: Codable {
    var conversations: [Conversation] = []
    var messages: [String: [Message]] = [:]
    var drafts: [String: String] = [:]
    var jobs: [String: [Job]] = [:]
    var pending: [OutgoingMessage] = []
    var pendingConversationID: String?
    var pendingConversationTitle: String?
    var pendingConversationParticipants: [String]?
    var selected: String?
    var identity: MembersResponse?
    var savedAt: Date = Date()
}
struct PrivateCache {
    let directory: URL
    init(session: SavedSession) throws {
        let tag = SHA256.hash(data: Data((session.address + "\n" + session.token).utf8)).map { String(format: "%02x", $0) }.joined()
        directory = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true).appendingPathComponent("AICollabHubPublicIOS/" + tag, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.protectionKey: FileProtectionType.complete])
        var url = directory; var values = URLResourceValues(); values.isExcludedFromBackup = true; try url.setResourceValues(values)
    }
    func read() -> LocalSnapshot? { guard let data = try? Data(contentsOf: directory.appendingPathComponent("snapshot.json")) else { return nil }; return try? JSONDecoder().decode(LocalSnapshot.self, from: data) }
    func write(_ snapshot: LocalSnapshot) throws {
        let data = try JSONEncoder().encode(snapshot)
        try data.write(to: directory.appendingPathComponent("snapshot.json"), options: [.atomic, .completeFileProtection])
    }
    func clear() { try? FileManager.default.removeItem(at: directory) }
}
