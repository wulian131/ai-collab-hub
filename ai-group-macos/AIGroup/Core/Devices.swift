import Foundation

public enum DeviceKind: String, Codable, CaseIterable, Identifiable, Sendable {
    case linux = "codex-linux", mac = "codex-macos", windows = "codex-windows", muse
    public var id: String { rawValue }
    public var label: String {
        switch self { case .linux: return "Codex · Linux"; case .mac: return "Codex · Mac"; case .windows: return "Codex · Windows / WSL2"; case .muse: return "Muse · 云端账号" }
    }
}
public struct DevicePermissions: Codable, Equatable, Sendable {
    public var read: Bool
    public var execute: Bool
    public var delegate: Bool
    public init(execute: Bool = false, delegate: Bool = false) { read = true; self.execute = execute; self.delegate = delegate }
    public var label: String { [read ? "读取" : nil, execute ? "执行" : nil, delegate ? "委派" : nil].compactMap { $0 }.joined(separator: "、") }
}
public struct Device: Decodable, Identifiable, Sendable {
    public let id: String
    public let name: String
    public let type: DeviceKind
    public let machine: String
    public let tags: [String]
    public let permissions: DevicePermissions
    public let state: String
    public let status: String
    public let lastSeen: Double?
    public let legacy: Bool?
    public let generation: Int?
    public var isLegacy: Bool { legacy == true }
    public var statusLabel: String {
        switch status { case "disabled": return "未启用"; case "invited": return "等待配对"; case "expired": return "邀请已过期"; case "waiting": return type == .muse ? "密钥就绪 · 待验证" : "已配对 · 等待连接"; case "connected": return "已连接"; case "offline": return "离线"; case "revoked": return "已撤销"; case "archived": return "已归档"; case "deleted": return "已删除"; default: return status }
    }
    enum CodingKeys: String, CodingKey { case id, name, type, machine, tags, permissions, state, status, legacy, generation; case lastSeen = "last_seen" }
}
public struct DeviceList: Decodable, Sendable { public let devices: [Device] }

// Keep the same request/key on a manual retry. The server never replays a secret.
public struct DeviceInviteRequest: Encodable, Equatable, Sendable {
    public let action: String
    public let key: String
    public let deviceID: String?
    public let name: String?
    public let type: DeviceKind?
    public let machine: String?
    public let tags: [String]?
    public let permissions: DevicePermissions?
    enum CodingKeys: String, CodingKey { case action, key, name, type, machine, tags, permissions; case deviceID = "device_id" }
    public init(name: String, type: DeviceKind, machine: String = "", permissions: DevicePermissions = .init(), key: String = UUID().uuidString) throws {
        let clean = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty, clean.count <= 60, !clean.unicodeScalars.contains(where: { $0.value < 32 }), machine.count <= 160, !machine.unicodeScalars.contains(where: { $0.value < 32 }) else { throw ClientError.validation("名称需为 1–60 字，位置最多 160 字，不能包含换行") }
        guard permissions.read, !permissions.delegate || permissions.execute else { throw ClientError.validation("委派需要执行权限") }
        action = "create"; self.key = key; deviceID = nil; self.name = clean; self.type = type; self.machine = machine; tags = []; self.permissions = permissions
    }
    public init(reinvite deviceID: String, key: String = UUID().uuidString) {
        action = "reinvite"; self.key = key; self.deviceID = deviceID; name = nil; type = nil; machine = nil; tags = nil; permissions = nil
    }
}
public struct DeviceInvitation: Decodable, Sendable {
    public let deviceID: String
    public let invitationID: String
    public var pairingCode: String?
    public let expires: Double?
    public let secretAvailable: Bool
    public let notice: String?
    public var hasValidCode: Bool { secretAvailable && pairingCode != nil && (expires ?? 0) > Date().timeIntervalSince1970 }
    enum CodingKeys: String, CodingKey { case expires, notice; case deviceID = "device_id"; case invitationID = "invitation_id"; case pairingCode = "pairing_code"; case secretAvailable = "secret_available" }
}
public enum DeviceManagementAction: String, Codable, Sendable { case revoke, archive, delete }
public struct DeviceActionRequest: Encodable, Sendable {
    public let deviceID: String
    public let action: String
    public let confirm: Bool
    enum CodingKeys: String, CodingKey { case action, confirm; case deviceID = "device_id" }
    public init(deviceID: String, action: DeviceManagementAction) { self.deviceID = deviceID; self.action = action.rawValue; confirm = action != .revoke }
    public init(deviceID: String, archive: Bool) { self.deviceID = deviceID; action = archive ? "archive" : "revoke"; confirm = archive }
}
public struct DeviceBundleRequest: Encodable, Sendable {
    public let deviceID: String
    public let invitationID: String?
    public let url: String
    enum CodingKeys: String, CodingKey { case url; case deviceID = "device_id"; case invitationID = "invitation_id" }
    public init(deviceID: String, invitationID: String?, url: String) { self.deviceID = deviceID; self.invitationID = invitationID; self.url = url }
}

// Desired values plus a list revision make manual retries safe and reject stale edits.
public struct DeviceSettingsRequest: Encodable, Equatable, Sendable {
    public let deviceID: String
    public let action: String
    public let generation: Int
    public let permissions: DevicePermissions?
    enum CodingKeys: String, CodingKey { case action, generation, permissions; case deviceID = "device_id" }
    public init(device: Device, permissions: DevicePermissions? = nil) throws {
        guard let generation = device.generation else { throw ClientError.validation("请升级服务端并刷新成员列表后再操作") }
        if let permissions, (permissions.execute && !permissions.read) || (permissions.delegate && !permissions.execute) {
            throw ClientError.validation("执行需要读取权限，委派需要执行权限")
        }
        deviceID = device.id; self.generation = generation; self.permissions = permissions
        action = permissions == nil ? "enable" : "permissions"
    }
}

public struct CloudCredentialRequest: Encodable, Equatable, Sendable {
    public let deviceID: String
    public let generation: Int
    public let key: String
    public let confirm = true
    enum CodingKeys: String, CodingKey { case generation, key, confirm; case deviceID = "device_id" }
    public init(deviceID: String, generation: Int, key: String = UUID().uuidString) {
        self.deviceID = deviceID; self.generation = generation; self.key = key
    }
}
public struct CloudCredential: Decodable, Sendable {
    public let memberID: String
    public var token: String?
    public let permissions: DevicePermissions
    public let secretAvailable: Bool
    public let displayUntil: Double?
    public let notice: String?
    public var canCopy: Bool { secretAvailable && token != nil && (displayUntil ?? 0) > Date().timeIntervalSince1970 }
    enum CodingKeys: String, CodingKey { case token, permissions, notice; case memberID = "member_id"; case secretAvailable = "secret_available"; case displayUntil = "display_until" }
}
