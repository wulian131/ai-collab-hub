import Foundation
public struct Endpoint: Equatable, Sendable {
    public let baseURL: URL
    public init(_ address: String, allowLoopbackHTTP: Bool = false) throws {
        let raw = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard var c = URLComponents(string: raw), let host = c.host, !host.isEmpty,
              c.user == nil, c.password == nil, c.query == nil, c.fragment == nil,
              c.scheme == "https" || (allowLoopbackHTTP && c.scheme == "http" && ["127.0.0.1", "localhost", "::1", "[::1]"].contains(host)),
              !c.path.contains(".."), !c.path.contains("\\"), !raw.contains("\n"), !raw.contains("\r")
        else { throw ClientError.validation("请输入完整的 HTTPS 服务地址，例如 https://chat.example.com/chat") }
        c.path = c.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        c.path = c.path.isEmpty ? "" : "/" + c.path
        guard let url = c.url else { throw ClientError.validation("服务地址无效") }
        baseURL = url
    }
    public func url(_ path: String, query: [URLQueryItem] = []) -> URL {
        var c = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)!
        c.path = c.path + "/api/" + path
        c.queryItems = query.isEmpty ? nil : query
        return c.url!
    }
    public var notificationsURL: URL {
        var c = URLComponents(url: url("notifications"), resolvingAgainstBaseURL: false)!
        c.scheme = baseURL.scheme == "https" ? "wss" : "ws"
        return c.url!
    }
}
