import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

// Redirects are refused so credentials cannot be forwarded to a different service.
final class NoRedirect: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}
public final class RoomAPI: @unchecked Sendable {
    public let endpoint: Endpoint
    private let token: String
    private let session: URLSession
    public init(endpoint: Endpoint, token: String) {
        self.endpoint = endpoint; self.token = token
        let config = URLSessionConfiguration.ephemeral
        config.urlCache = nil; config.httpCookieStorage = nil
        config.timeoutIntervalForRequest = 25; config.timeoutIntervalForResource = 60
        session = URLSession(configuration: config, delegate: NoRedirect(), delegateQueue: nil)
    }
    public func close() { session.invalidateAndCancel() }
    public func request<T: Decodable>(_ path: String, method: String = "GET", query: [URLQueryItem] = [], body: Data? = nil, contentType: String = "application/json") async throws -> T {
        let data = try await raw(path, method: method, query: query, body: body, contentType: contentType)
        do { return try JSONDecoder().decode(T.self, from: data) } catch { throw ClientError.invalidResponse }
    }
    public func raw(_ path: String, method: String = "GET", query: [URLQueryItem] = [], body: Data? = nil, contentType: String = "application/json") async throws -> Data {
        var request = URLRequest(url: endpoint.url(path, query: query))
        request.httpMethod = method; request.httpBody = body
        request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        request.setValue(contentType, forHTTPHeaderField: "Content-Type")
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw ClientError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            let error = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            throw ClientError.server(http.statusCode, error?["error"] as? String ?? "请求失败，请检查服务地址或稍后重试")
        }
        guard data.count <= 12 * 1024 * 1024 else { throw ClientError.validation("返回内容过大，请缩小读取范围") }
        return data
    }
    public func members() async throws -> MembersResponse { try await request("members") }
    public func devices() async throws -> DeviceList { try await request("devices") }
    public func inviteDevice(_ payload: DeviceInviteRequest) async throws -> DeviceInvitation {
        try await request("devices/invite", method: "POST", body: JSONEncoder().encode(payload))
    }
    public func cloudCredential(_ request: CloudCredentialRequest) async throws -> CloudCredential {
        try await self.request("devices/cloud-credential", method: "POST", body: JSONEncoder().encode(request))
    }
    public func configureDevice(_ payload: DeviceSettingsRequest) async throws -> EmptyResponse {
        try await request("devices/action", method: "POST", body: JSONEncoder().encode(payload))
    }
    public func deviceAction(_ payload: DeviceActionRequest) async throws -> EmptyResponse {
        try await request("devices/action", method: "POST", body: JSONEncoder().encode(payload))
    }
    public func deviceBundle(deviceID: String, invitationID: String?) async throws -> Data {
        try await raw("devices/bundle", method: "POST", body: JSONEncoder().encode(DeviceBundleRequest(deviceID: deviceID, invitationID: invitationID, url: endpoint.baseURL.absoluteString)))
    }
    public func conversations(search: String = "", offset: Int = 0) async throws -> ConversationList {
        try await request("conversations", query: [.init(name: "q", value: search), .init(name: "offset", value: String(offset))])
    }
    public func saveConversation(id: String = UUID().uuidString, title: String, rename: Bool = false, participants: [String]? = nil) async throws -> ConversationResponse {
        var payload: [String: Any] = ["id": id, "title": title, "rename": rename]
        if !rename, let participants { payload["participants"] = Array(Set(participants)).sorted() }
        return try await request("conversations", method: "POST", body: JSONSerialization.data(withJSONObject: payload))
    }
    public func messages(conversationID: String, after: Int = 0, before: Int = 0) async throws -> MessageList {
        try await request("messages", query: [.init(name: "conversation_id", value: conversationID), .init(name: "after", value: String(after)), .init(name: "before", value: String(before)), .init(name: "limit", value: "100")])
    }
    public func jobs(conversationID: String, before: Int = 0) async throws -> JobList {
        try await request("jobs", query: [.init(name: "conversation_id", value: conversationID), .init(name: "before", value: String(before)), .init(name: "limit", value: "50")])
    }
    public func send(_ payload: OutgoingMessage) async throws -> SendResponse { try await request("messages", method: "POST", body: JSONEncoder().encode(payload)) }
    public func retry(jobID: Int, reason: String) async throws -> EmptyResponse {
        let body = try JSONSerialization.data(withJSONObject: ["job_id": jobID, "reason": reason])
        return try await request("jobs/retry", method: "POST", body: body)
    }
    public func upload(data: Data, name: String, conversationID: String, id: String = UUID().uuidString.lowercased()) async throws -> Attachment {
        guard data.count <= 10 * 1024 * 1024 else { throw ClientError.validation("单个附件最多 10 MB") }
        let body = try JSONSerialization.data(withJSONObject: ["id": id, "name": name, "size": data.count, "conversation_id": conversationID])
        let start: UploadResponse = try await request("attachments", method: "POST", body: body)
        var offset = start.offset
        guard let attachment = start.attachment, offset >= 0, offset <= data.count else { throw ClientError.invalidResponse }
        while offset < data.count {
            let end = min(offset + 512 * 1024, data.count)
            let chunk: UploadResponse = try await request("attachments/" + id, method: "POST", query: [.init(name: "offset", value: String(offset))], body: data.subdata(in: offset..<end), contentType: "application/octet-stream")
            guard chunk.offset == end else { throw ClientError.invalidResponse }
            offset = chunk.offset
        }
        return attachment
    }
    public func removeAttachment(_ id: String) async throws -> EmptyResponse { try await request("attachments/" + id + "/remove", method: "POST", body: Data("{}".utf8)) }
    public func download(_ attachment: Attachment) async throws -> Data { try await raw("attachments/" + attachment.id) }
    public func notifications() -> URLSessionWebSocketTask {
        session.webSocketTask(with: endpoint.notificationsURL)
    }
    public func authenticateNotifications(_ socket: URLSessionWebSocketTask) async throws {
        let body = try JSONEncoder().encode(["token": token])
        try await socket.send(.string(String(decoding: body, as: UTF8.self)))
    }
}
