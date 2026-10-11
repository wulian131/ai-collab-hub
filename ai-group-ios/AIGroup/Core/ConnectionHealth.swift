import Foundation

public enum FailurePresentation {
    public static func isCancellation(_ failure: Error) -> Bool {
        failure is CancellationError || (failure as? URLError)?.code == .cancelled
    }
    public static func isConnectivity(_ failure: Error) -> Bool {
        if let error = failure as? URLError {
            return [.timedOut, .networkConnectionLost, .notConnectedToInternet, .cannotFindHost,
                    .cannotConnectToHost, .dnsLookupFailed, .internationalRoamingOff, .callIsActive,
                    .dataNotAllowed, .resourceUnavailable, .backgroundSessionWasDisconnected].contains(error.code)
        }
        if case .server(let status, _) = failure as? ClientError { return [408, 429, 500, 502, 503, 504].contains(status) }
        return false
    }
}

// Both transports must be healthy before the app claims a live connection.
public struct ConnectionHealth: Equatable, Sendable {
    public var foreground = false
    public var authenticated = false
    public var notificationsReady = false
    public var httpUnavailable = false
    public var loginExpired = false
    public init() {}
    public var label: String {
        if loginExpired { return "登录已失效" }
        guard authenticated else { return "离线" }
        guard foreground else { return "后台暂停" }
        return notificationsReady && !httpUnavailable ? "实时连接" : "连接中"
    }
    public var notice: String? {
        guard foreground else { return nil }
        if authenticated && (!notificationsReady || httpUnavailable) { return "连接中，正在重新连接…" }
        if httpUnavailable && !authenticated { return "网络暂不可用，请检查网络后重试" }
        return nil
    }
}
