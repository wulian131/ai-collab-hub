import SwiftUI
import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

@MainActor final class RoomStore: ObservableObject {
    @Published var identity: MembersResponse?
    @Published var conversations: [Conversation] = []
    @Published var conversationHasMore = false
    @Published var selectedID: String?
    @Published var messages: [Message] = []
    @Published var messageHasMore = false
    @Published var jobs: [Job] = []
    @Published var jobHasMore = false
    @Published var pending: [OutgoingMessage] = []
    @Published var draft = ""
    @Published var reply: Message?
    @Published var uploads: [Attachment] = []
    @Published var uploading = false
    @Published var sending = false
    @Published var loading = false
    @Published var savingConversation = false
    @Published var conversationParticipants: [String]?
    @Published var pendingConversationParticipants: [String]?
    @Published var pendingConversationTitle: String?
    private var pendingConversationID: String?
    @Published var error: String?
    @Published var receiptText: String?
    @Published var health = ConnectionHealth()
    var connection: String { health.label }
    var connectionNotice: String? { health.notice }
    @Published var lastSync: Date?
    @Published var address = ""
    private var api: RoomAPI?
    private let apiFactory: (Endpoint, String) -> RoomAPI
    init(apiFactory: @escaping (Endpoint, String) -> RoomAPI = { RoomAPI(endpoint: $0, token: $1) }) { self.apiFactory = apiFactory }
    private var cache: PrivateCache?
    private var snapshot = LocalSnapshot()
    private var socket: URLSessionWebSocketTask?
    private var notificationTask: Task<Void, Never>?
    private var recoveryTask: Task<Void, Never>?
    private var foreground = false
    private var generation = 0
    private var conversationOffset = 0
    private var currentSearch = ""
    private var refreshRunning = false
    private var refreshAgain = false
    var members: [Member] { identity?.members ?? [] }
    var canWrite: Bool { identity?.canWrite == true }
    var canManageDevices: Bool { identity?.canManageDevices == true }
    func deviceAPI() throws -> RoomAPI {
        guard canManageDevices, let api else { throw ClientError.validation("请使用管理员身份联网后添加成员") }
        return api
    }
    func isCurrentDeviceAPI(_ candidate: RoomAPI) -> Bool { canManageDevices && api === candidate }
    func refreshMembers() async {
        guard let api else { return }
        let epoch = generation
        do {
            let result = try await api.members()
            guard generation == epoch else { return }
            identity = result; try saveLocal()
        } catch { if generation == epoch { report(error) } }
    }
    var recoveryMemberName: String? { snapshot.identity?.members.first { $0.id == snapshot.identity?.me }?.name }
    var isConnected: Bool { identity != nil }
    var selectedConversation: Conversation? { conversations.first { $0.id == selectedID } ?? snapshot.conversations.first { $0.id == selectedID } }
    var conversationMembers: [Member] { members.filter { !$0.isRetired && $0.enabled != false && (conversationParticipants == nil || conversationParticipants!.contains($0.id)) } }
    var conversationExecutors: [Member] { executors.filter { conversationParticipants == nil || conversationParticipants!.contains($0.id) } }
    var defaultConversationTargets: Set<String> { conversationParticipants == nil ? [] : Set(conversationExecutors.map(\.id)) }
    var roomCandidates: [Member] { members.filter { $0.role == "agent" && !$0.isRetired && $0.enabled != false && $0.permissions?["read"] != false && $0.id != identity?.me } }
    var executors: [Member] { members.filter { $0.canExecute && $0.id != identity?.me } }
    func memberName(_ id: String) -> String { members.first { $0.id == id }?.name ?? (id == "hub" ? "群系统" : id) }

    func restore() async {
        #if DEBUG
        if ProcessInfo.processInfo.environment["MAC_SYNTHETIC_TEST"] == "1", let url = ProcessInfo.processInfo.environment["MAC_FIXTURE_URL"] {
            do { try await connect(address: url, token: "ios-owner-fixture") } catch { report(error) }; return
        }
        #endif
        do { if let saved = try KeychainStore.read() { try await connect(address: saved.address, token: saved.token, remember: false) } }
        catch { report(error) }
    }
    func connect(address: String, token: String, remember: Bool = true) async throws {
        guard !loading else { return }
        loading = true; defer { loading = false }
        let endpoint = try Endpoint(address, allowLoopbackHTTP: ProcessInfo.processInfo.environment["MAC_SYNTHETIC_TEST"] == "1"), clean = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty, clean.count <= 256, !clean.contains("\n"), !clean.contains("\r") else { throw ClientError.validation("请输入有效登录凭证") }
        let candidate = apiFactory(endpoint, clean)
        let saved = SavedSession(address: endpoint.baseURL.absoluteString, token: clean)
        let nextCache: PrivateCache
        do { nextCache = try PrivateCache(session: saved) } catch { candidate.close(); throw error }
        let stored = nextCache.read() ?? LocalSnapshot()
        if !remember { self.address = saved.address; self.cache = nextCache; self.snapshot = stored }
        do {
            let me = try await candidate.members()
            var recovered = stored
            // Migrate drafts only after proving the same server and member identity.
            // A replacement key must never expose one account's cache to another.
            if remember, self.address == saved.address, snapshot.identity?.me == me.me, cache != nil {
                if identity != nil || selectedID != nil { try saveLocal() }; recovered = snapshot
                try nextCache.write(recovered)
            }
            if remember { try KeychainStore.write(saved) }
            activate(candidate, me: me, saved: saved, cache: nextCache, snapshot: recovered)
            await refreshConversations()
            if selectedID == nil, let first = conversations.first { select(first.id) }
            await refreshActive()
            if foreground { startNotifications() }
        } catch {
            // A previously authenticated identity may read its protected cache offline.
            // Server/authentication failures never become a new offline login.
            if !remember, FailurePresentation.isConnectivity(error), let me = stored.identity {
                activate(candidate, me: me, saved: saved, cache: nextCache, snapshot: stored)
                health.httpUnavailable = true
                startRecovery()
                if foreground { startNotifications() }
            } else { candidate.close(); throw error }
        }
    }
    private func activate(_ candidate: RoomAPI, me: MembersResponse, saved: SavedSession, cache nextCache: PrivateCache, snapshot stored: LocalSnapshot) {
        stopNotifications(); api?.close(); generation += 1
        if let cache, cache.directory != nextCache.directory { cache.clear() }
        api = candidate; identity = me; address = saved.address; cache = nextCache
        snapshot = stored; pending = stored.pending; conversations = stored.conversations
        selectedID = stored.selected; messages = stored.messages[selectedID ?? ""] ?? []
        draft = stored.drafts[selectedID ?? ""] ?? ""; lastSync = stored.identity == nil ? nil : stored.savedAt
        jobs = stored.jobs[selectedID ?? ""] ?? []; uploads = []; reply = nil; sending = false; uploading = false; savingConversation = false; pendingConversationID = stored.pendingConversationID; pendingConversationTitle = stored.pendingConversationTitle; pendingConversationParticipants = stored.pendingConversationParticipants; conversationParticipants = stored.conversations.first { $0.id == selectedID }?.participants
        error = nil; health.authenticated = true; health.loginExpired = false; health.httpUnavailable = false
    }

    func logout() {
        stopNotifications(); api?.close(); api = nil; generation += 1; cache?.clear(); cache = nil; KeychainStore.clear()
        identity = nil; conversations = []; selectedID = nil; messages = []; jobs = []; pending = []; draft = ""; reply = nil; uploads = []
        snapshot = LocalSnapshot(); conversationParticipants = nil; error = nil; receiptText = nil; lastSync = nil; health = ConnectionHealth(); health.foreground = foreground; sending = false; uploading = false; savingConversation = false; pendingConversationID = nil; pendingConversationTitle = nil; pendingConversationParticipants = nil
    }
    func setForeground(_ active: Bool) {
        foreground = active
        health.foreground = active
        if active, api != nil, identity != nil { startNotifications(); startRecovery(); Task { await refreshConversations(); await refreshActive() } }
        else { stopNotifications() }
    }
    func saveLocal() throws {
        snapshot.pending = pending; snapshot.pendingConversationID = pendingConversationID; snapshot.pendingConversationTitle = pendingConversationTitle; snapshot.pendingConversationParticipants = pendingConversationParticipants; snapshot.selected = selectedID; if let identity { snapshot.identity = identity }
        if let id = selectedID { snapshot.drafts[id] = draft; snapshot.messages[id] = Array(messages.suffix(100)); snapshot.jobs[id] = Array(jobs.prefix(50)) }
        // Keep only the most recent 20 cached threads; drafts and uncertain sends remain available.
        let keep = Set(snapshot.conversations.prefix(20).map(\.id) + pending.map(\.conversationID) + [selectedID ?? ""])
        snapshot.messages = snapshot.messages.filter { keep.contains($0.key) }; snapshot.jobs = snapshot.jobs.filter { keep.contains($0.key) }
        snapshot.savedAt = lastSync ?? Date(); try cache?.write(snapshot)
    }
    func saveDraft() { do { try saveLocal() } catch { self.error = "草稿保存失败：" + error.localizedDescription } }
    func report(_ failure: Error) {
        guard !FailurePresentation.isCancellation(failure) else { return }
        if FailurePresentation.isConnectivity(failure) {
            health.httpUnavailable = true
            startRecovery()
            return
        }
        if let apiError = failure as? ClientError, apiError.isUnauthorized { if identity != nil { try? saveLocal() }; stopNotifications(); api?.close(); api = nil; generation += 1; sending = false; uploading = false; savingConversation = false; health.loginExpired = true; health.authenticated = false; identity = nil }
        error = failure.localizedDescription
    }
    func refreshConversations(search: String = "", more: Bool = false) async {
        guard let api else { return }
        if !more { currentSearch = search; conversationOffset = 0 }
        let epoch = generation, query = currentSearch, offset = more ? conversationOffset : 0
        do {
            let result = try await api.conversations(search: query, offset: offset)
            guard epoch == generation, query == currentSearch else { return }
            if more { var seen = Set(conversations.map(\.id)); conversations += result.conversations.filter { seen.insert($0.id).inserted } }
            else { conversations = result.conversations }
            conversationOffset = offset + result.conversations.count; conversationHasMore = result.hasMore
            if query.isEmpty { snapshot.conversations = conversations; try saveLocal() }
        } catch { if epoch == generation { report(error) } }
    }
    func select(_ id: String) {
        guard selectedID != id else { return }
        saveDraft(); selectedID = id; conversationParticipants = snapshot.conversations.first { $0.id == id }?.participants; messages = snapshot.messages[id] ?? []; draft = snapshot.drafts[id] ?? ""
        reply = nil; uploads = []; jobs = snapshot.jobs[id] ?? []; messageHasMore = false; jobHasMore = false; receiptText = nil
        Task { await refreshActive() }
    }
    func refreshActive() async {
        guard let api, let id = selectedID else { return }
        if refreshRunning { refreshAgain = true; return }
        refreshRunning = true
        defer { refreshRunning = false; if refreshAgain { refreshAgain = false; Task { await refreshActive() } } }
        let epoch = generation
        do {
            let list = try await api.members()
            guard epoch == generation, selectedID == id else { return }
            identity = list
            var after = messages.last?.id ?? 0
            for _ in 0..<20 {
                let result = try await api.messages(conversationID: id, after: after)
                guard epoch == generation, selectedID == id else { return }
                if let conversation = result.conversation {
                    conversationParticipants = conversation.participants
                    snapshot.conversations.removeAll { $0.id == id }; snapshot.conversations.append(conversation)
                }
                messages = MessageMerge.merge(messages, result.messages, conversationID: id)
                if after == 0 { messageHasMore = result.hasMore }
                guard result.hasMore && !result.messages.isEmpty && after != 0 else { break }
                after = result.messages.last!.id
            }
            let result = try await api.jobs(conversationID: id)
            guard epoch == generation, selectedID == id else { return }
            jobs = result.jobs.filter { $0.conversationID == id }; jobHasMore = result.hasMore
            lastSync = Date(); health.httpUnavailable = false; try saveLocal()
        } catch { if epoch == generation, selectedID == id { report(error) } }
    }
    func earlierMessages() async {
        guard let api, let id = selectedID, let before = messages.first?.id else { return }
        let epoch = generation
        do {
            let result = try await api.messages(conversationID: id, before: before)
            guard epoch == generation, selectedID == id else { return }
            messages = MessageMerge.merge(messages, result.messages, conversationID: id); messageHasMore = result.hasMore; try saveLocal()
        } catch { if epoch == generation { report(error) } }
    }
    func earlierJobs() async {
        guard let api, let id = selectedID, let before = jobs.last?.id else { return }; let epoch = generation
        do { let result = try await api.jobs(conversationID: id, before: before); guard epoch == generation, selectedID == id else { return }; let seen = Set(jobs.map(\.id)); jobs += result.jobs.filter { $0.conversationID == id && !seen.contains($0.id) }; jobHasMore = result.hasMore } catch { if epoch == generation { report(error) } }
    }
    @discardableResult
    func saveConversation(title: String, rename: Bool = false, participants: [String]? = nil) async -> String? {
        guard canWrite, !savingConversation, let api else { return nil }
        let epoch = generation
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed.count <= 100 else { error = "标题需包含 1–100 个字符"; return nil }
        let id: String
        let requestedTitle: String
        let requestedParticipants = rename ? nil : pendingConversationID != nil ? pendingConversationParticipants : participants.map { Array(Set($0)).sorted() }
        if !rename, pendingConversationID == nil, let requestedParticipants {
            guard !requestedParticipants.isEmpty, requestedParticipants.count <= 20, requestedParticipants.allSatisfy({ id in roomCandidates.contains { $0.id == id } }) else { error = "请选择 1–20 位已启用的对话成员"; return nil }
        }
        if rename {
            guard let selectedID else { return nil }; id = selectedID; requestedTitle = trimmed
        } else {
            id = pendingConversationID ?? UUID().uuidString
            requestedTitle = pendingConversationTitle ?? trimmed
            pendingConversationID = id; pendingConversationTitle = requestedTitle; pendingConversationParticipants = requestedParticipants
        }
        do { try saveLocal() } catch { report(error); return nil }
        savingConversation = true; error = nil
        defer { if epoch == generation { savingConversation = false } }
        do {
            let result = try await api.saveConversation(id: id, title: requestedTitle, rename: rename, participants: requestedParticipants)
            guard epoch == generation, result.conversation.id == id else { return nil }
            if !rename, let requestedParticipants, result.conversation.participants != requestedParticipants { throw ClientError.validation("服务端尚未支持对话成员，请更新服务端后重试原请求") }
            if !rename { pendingConversationID = nil; pendingConversationTitle = nil; pendingConversationParticipants = nil }
            snapshot.conversations.removeAll { $0.id == id }; snapshot.conversations.insert(result.conversation, at: 0)
            await refreshConversations(search: currentSearch)
            guard epoch == generation else { return nil }
            if !rename { select(id) }
            saveDraft()
            return id
        } catch {
            if epoch == generation {
                if let failure = error as? ClientError, case .server(let code, _) = failure, code < 500, ![408, 429].contains(code), !rename { pendingConversationID = nil; pendingConversationTitle = nil; pendingConversationParticipants = nil }
                report(error); saveDraft()
            }
            return nil
        }
    }

    func send(targets: [String], leader: String?) async {
        guard canWrite, !sending, !uploading, let id = selectedID else { return }
        do {
            let payload = try OutgoingMessage(text: draft, conversationID: id, dispatch: true, targets: targets, leader: leader, replyTo: reply?.id, attachments: uploads.map(\.id))
            pending.append(payload)
            do { try saveLocal() } catch { pending.removeAll { $0.key == payload.key }; throw ClientError.validation("未能安全保存待发送消息，请重试") }
            draft = ""; reply = nil; uploads = []; saveDraft()
            await retrySend(payload)
        } catch { report(error) }
    }
    func retrySend(_ payload: OutgoingMessage) async {
        guard let api, canWrite, !sending else { return }
        sending = true; if selectedID == payload.conversationID { receiptText = nil }; let epoch = generation
        defer { if epoch == generation { sending = false } }
        do {
            let result = try await api.send(payload)
            guard epoch == generation else { return }
            pending.removeAll { $0.key == payload.key }
            if selectedID == payload.conversationID {
                messages = MessageMerge.merge(messages, [result.message], conversationID: payload.conversationID)
                receiptText = result.receipt.jobs.isEmpty ? "消息已保存" : "收到 · 任务 #\(result.receipt.taskID ?? result.message.id) · " + result.receipt.jobs.map { memberName($0.target) }.joined(separator: "、")
            }
            try saveLocal(); await refreshActive(); await refreshConversations()
        } catch { if epoch == generation { report(error) } }
    }
    func cancelPending(_ payload: OutgoingMessage) { pending.removeAll { $0.key == payload.key }; saveDraft() }
    func retryJob(_ job: Job, reason: String) async {
        guard identity?.me == "owner", canWrite, let api else { return }; let epoch = generation
        do { let _: EmptyResponse = try await api.retry(jobID: job.id, reason: reason); if epoch == generation { await refreshActive() } } catch { if epoch == generation { report(error) } }
    }
    func addFile(data: Data, name: String) async {
        guard canWrite, !uploading, let api, let id = selectedID else { return }
        guard uploads.count < 5 else { error = "每条消息最多 5 个附件"; return }
        uploading = true; let epoch = generation
        defer { if epoch == generation { uploading = false } }
        do { let attachment = try await api.upload(data: data, name: name, conversationID: id); guard epoch == generation, selectedID == id else { return }; uploads.append(attachment) } catch { if epoch == generation { report(error) } }
    }
    func removeUpload(_ item: Attachment) async {
        guard let api else { return }; let epoch = generation
        do { let _: EmptyResponse = try await api.removeAttachment(item.id); if epoch == generation { uploads.removeAll { $0.id == item.id } } } catch { if epoch == generation { report(error) } }
    }
    func download(_ item: Attachment) async throws -> URL {
        guard let api, let cache else { throw ClientError.validation("请先登录") }
        let data = try await api.download(item)
        let folder = cache.directory.appendingPathComponent("attachments/" + item.id, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let safe = item.name.replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "\\", with: "_")
        let url = folder.appendingPathComponent(safe == "." || safe == ".." ? "attachment" : safe)
        try data.write(to: url, options: [.atomic]); return url
    }
    private func startNotifications() {
        guard foreground, api != nil, notificationTask == nil else { return }
        notificationTask = Task { [weak self] in await self?.listen() }
    }
    private func stopNotifications() { notificationTask?.cancel(); notificationTask = nil; socket?.cancel(with: .goingAway, reason: nil); socket = nil; health.notificationsReady = false; recoveryTask?.cancel(); recoveryTask = nil }
    private func startRecovery() {
        guard foreground, api != nil, health.httpUnavailable, recoveryTask == nil else { return }
        let epoch = generation
        recoveryTask = Task { [weak self] in
            var delay: UInt64 = 2
            while !Task.isCancelled {
                do { try await Task.sleep(nanoseconds: delay * 1_000_000_000) } catch { return }
                guard let self, self.foreground, self.generation == epoch, let api = self.api else { return }
                guard self.health.httpUnavailable else { self.recoveryTask = nil; return }
                do {
                    let identity = try await api.members()
                    guard !Task.isCancelled, self.foreground, self.generation == epoch else { return }
                    self.identity = identity; self.health.httpUnavailable = false
                    await self.refreshConversations(search: self.currentSearch); await self.refreshActive()
                    if !self.health.httpUnavailable { self.recoveryTask = nil; return }
                } catch {
                    guard !Task.isCancelled, self.foreground, self.generation == epoch else { return }
                    if FailurePresentation.isCancellation(error) { self.recoveryTask = nil; return }
                    if !FailurePresentation.isConnectivity(error) { self.recoveryTask = nil; self.report(error); return }
                }
                delay = min(delay * 2, 30)
            }
        }
    }
    private func listen() async {
        var delay: UInt64 = 1
        let epoch = generation
        while !Task.isCancelled, foreground, epoch == generation, let api {
            let ws = api.notifications(); socket = ws; ws.resume(); health.notificationsReady = false
            do {
                try await api.authenticateNotifications(ws)
                while !Task.isCancelled {
                    let received = try await ws.receive(); let data: Data
                    switch received { case .string(let value): data = Data(value.utf8); case .data(let value): data = value; @unknown default: continue }
                    guard data.count <= 8192, let value = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw ClientError.invalidResponse }
                    guard epoch == generation, !Task.isCancelled else { return }
                    if value["type"] as? String == "error" { throw ClientError.server(401, "实时连接身份验证失败，请重新登录") }
                    if value["type"] as? String == "state" { health.notificationsReady = true; delay = 1; await refreshActive(); await refreshConversations(search: currentSearch) }
                }
            } catch {
                ws.cancel(with: .goingAway, reason: nil)
                guard !Task.isCancelled, foreground, epoch == generation else { return }
                if let e = error as? ClientError, e.isUnauthorized { report(e); return }
                health.notificationsReady = false
                try? await Task.sleep(nanoseconds: delay * 1_000_000_000); delay = min(delay * 2, 30)
            }
        }
    }
}
