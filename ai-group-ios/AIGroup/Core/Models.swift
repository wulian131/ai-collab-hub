import Foundation

public struct Member: Codable, Identifiable, Equatable, Sendable {
    public let id: String
    public let name: String
    public let role: String
    public var online: Bool?
    public var enabled: Bool?
    public var state: String?
    public var isRetired: Bool { state == "archived" || state == "deleted" }
    public var aliases: [String]?
    public var machine: String?
    public var activity: String?
    public var permissions: [String: Bool]?
    public var canExecute: Bool { role == "agent" && !isRetired && enabled != false && permissions?["execute"] != false }
    public var initials: String { String(name.prefix(2)) }
}
public struct MembersResponse: Codable, Sendable {
    public let me: String
    public let readonly: Bool
    public let members: [Member]
    public var canWrite: Bool { !readonly && members.contains { $0.id == me && $0.role == "human" } }
    public var canManageDevices: Bool { me == "owner" && canWrite }
}
public struct Conversation: Codable, Identifiable, Hashable, Sendable {
    public let id: String
    public var title: String
    public let created: Double
    public var updated: Double
    public var summary: String?
    public var participants: [String]? = nil
}
public struct ConversationList: Codable, Sendable {
    public let conversations: [Conversation]
    public let hasMore: Bool
    enum CodingKeys: String, CodingKey { case conversations; case hasMore = "has_more" }
}
public struct ConversationResponse: Codable, Sendable { public let conversation: Conversation }
public struct Attachment: Codable, Identifiable, Equatable, Sendable {
    public let id: String
    public let name: String
    public let size: Int
    public let conversationID: String
    enum CodingKeys: String, CodingKey { case id, name, size; case conversationID = "conversation_id" }
}
public struct Message: Codable, Identifiable, Equatable, Sendable {
    public let id: Int
    public let author: String
    public let text: String
    public let created: Double
    public let conversationID: String
    public let replyTo: Int?
    public let targets: [String]
    public let source: String?
    public let attachments: [Attachment]
    public let key: String?
    enum CodingKeys: String, CodingKey { case id, author, text, created, targets, source, attachments, key; case conversationID = "conversation_id"; case replyTo = "reply_to" }
}
public struct MessageList: Codable, Sendable {
    public let conversation: Conversation?
    public let messages: [Message]
    public let hasMore: Bool
    public let conversationID: String
    enum CodingKeys: String, CodingKey { case messages, conversation; case hasMore = "has_more"; case conversationID = "conversation_id" }
}
public struct TaskEvent: Codable, Sendable { public let state: String; public let created: Double }
public struct Job: Codable, Identifiable, Sendable {
    public let id: Int
    public let messageID: Int
    public let target: String
    public let status: String
    public let attempts: Int
    public let conversationID: String
    public let completed: Int?
    public let blocked: Int?
    public let events: [TaskEvent]
    enum CodingKeys: String, CodingKey { case id, target, status, attempts, completed, blocked, events; case messageID = "message_id"; case conversationID = "conversation_id" }
    public var stateLabel: String {
        if blocked == 1 { return "等待补充" }
        switch status {
        case "pending": return "收到 · 已入队"
        case "running": return events.contains { $0.state == "processing" } ? "处理中" : "已领取"
        case "waiting": return "等待协作"
        case "failed": return "失败"
        case "done": return completed == 1 ? "完成" : "阶段已返回"
        default: return status
        }
    }
}
public struct JobList: Codable, Sendable {
    public let jobs: [Job]
    public let hasMore: Bool
    enum CodingKeys: String, CodingKey { case jobs; case hasMore = "has_more" }
}
public struct ReceiptJob: Codable, Sendable { public let id: Int; public let target: String }
public struct Receipt: Codable, Sendable {
    public let text: String
    public let taskID: Int?
    public let conversationID: String
    public let jobs: [ReceiptJob]
    enum CodingKeys: String, CodingKey { case text, jobs; case taskID = "task_id"; case conversationID = "conversation_id" }
}
public struct SendResponse: Codable, Sendable { public let message: Message; public let receipt: Receipt }
public struct EmptyResponse: Codable, Sendable { public let ok: Bool? }
public struct UploadResponse: Codable, Sendable { public let attachment: Attachment?; public let offset: Int }

// A frozen payload is reused unchanged after uncertain network results, even after an app restart.
public struct OutgoingMessage: Codable, Equatable, Sendable {
    public let text: String
    public let conversationID: String
    public let key: String
    public let source: String
    public let dispatch: Bool
    public let broadcast: Bool
    public let targets: [String]
    public let leader: String?
    public let replyTo: Int?
    public let attachments: [String]
    enum CodingKeys: String, CodingKey { case text, key, source, dispatch, broadcast, targets, leader, attachments; case conversationID = "conversation_id"; case replyTo = "reply_to" }
    public init(text: String, conversationID: String, key: String = UUID().uuidString, dispatch: Bool, targets: [String] = [], leader: String? = nil, replyTo: Int? = nil, attachments: [String] = []) throws {
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !attachments.isEmpty else { throw ClientError.validation("先输入消息或添加附件") }
        guard text.count <= 16000 else { throw ClientError.validation("消息最多 16000 个字符") }
        guard !dispatch || !targets.isEmpty else { throw ClientError.validation("请选择执行成员") }
        guard !dispatch || targets.count <= 20 else { throw ClientError.validation("一次最多分派给 20 位成员") }
        guard leader == nil || (dispatch && targets.contains(leader!)) else { throw ClientError.validation("负责人必须是本次执行成员") }
        self.text = text; self.conversationID = conversationID; self.key = key; self.source = "user"
        self.dispatch = dispatch; self.broadcast = !dispatch; self.targets = dispatch ? Array(Set(targets)).sorted() : []
        self.leader = dispatch ? leader : nil; self.replyTo = replyTo; self.attachments = attachments
    }
}
public enum ClientError: Error, LocalizedError, Equatable {
    case validation(String), server(Int, String), invalidResponse
    public var errorDescription: String? {
        switch self { case .validation(let text): return text; case .server(_, let text): return text; case .invalidResponse: return "服务返回的数据无法读取，请重试" }
    }
    public var isUnauthorized: Bool { if case .server(401, _) = self { return true }; return false }
}
public enum MessageMerge {
    public static func merge(_ old: [Message], _ incoming: [Message], conversationID: String, limit: Int? = nil) -> [Message] {
        var index = Dictionary(old.filter { $0.conversationID == conversationID }.map { ($0.id, $0) }, uniquingKeysWith: { _, b in b })
        for message in incoming where message.conversationID == conversationID { index[message.id] = message }
        let sorted = index.values.sorted { $0.id < $1.id }
        return limit.map { Array(sorted.suffix($0)) } ?? sorted
    }
}

public enum TaskFilter: String, CaseIterable, Sendable {
    case active, attention, all
    public var title: String { switch self { case .active: return "进行中"; case .attention: return "需处理"; case .all: return "全部" } }
    public func includes(_ job: Job) -> Bool {
        switch self {
        case .active: return job.blocked != 1 && ["pending", "running", "waiting"].contains(job.status)
        case .attention: return job.status == "failed" || job.blocked == 1
        case .all: return true
        }
    }
}
public struct ComposerSelection: Equatable, Sendable {
    public let targets: Set<String>
    public let leader: String?
    public init(targets: Set<String>, leader: String?, members: [Member]) {
        let valid = targets.intersection(members.filter(\.canExecute).map(\.id))
        self.targets = valid
        self.leader = leader.flatMap { valid.contains($0) ? $0 : nil }
    }
}
