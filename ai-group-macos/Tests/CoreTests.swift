import XCTest
@testable import AIGroupCore
final class CoreTests: XCTestCase {
    func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T { try JSONDecoder().decode(type, from: Data(json.utf8)) }
    func testConversationParticipantsDecodeWithLegacyCompatibility() throws {
        let old = try decode(Conversation.self, #"{"id":"main","title":"Old","created":1,"updated":2}"#)
        XCTAssertNil(old.participants)
        let scoped = try decode(Conversation.self, #"{"id":"scoped-room","title":"Team","created":1,"updated":2,"participants":["codex-fixture-01"]}"#)
        XCTAssertEqual(scoped.participants, ["codex-fixture-01"])
    }
    func testTaskFiltersKeepBlockedAndReturnedDistinct() throws {
        func job(_ status: String, completed: Int = 0, blocked: Int = 0) throws -> Job {
            try decode(Job.self, "{\"id\":1,\"message_id\":7,\"target\":\"codex-fixture-01\",\"status\":\"\(status)\",\"completed\":\(completed),\"blocked\":\(blocked),\"attempts\":1,\"conversation_id\":\"main\",\"events\":[]}")
        }
        for status in ["pending", "running", "waiting"] { XCTAssertTrue(TaskFilter.active.includes(try job(status))) }
        XCTAssertFalse(TaskFilter.active.includes(try job("running", blocked: 1)))
        XCTAssertTrue(TaskFilter.attention.includes(try job("done", blocked: 1)))
        XCTAssertTrue(TaskFilter.attention.includes(try job("failed")))
        XCTAssertFalse(TaskFilter.attention.includes(try job("done", completed: 1)))
        XCTAssertFalse(TaskFilter.active.includes(try job("done")))
        XCTAssertTrue(TaskFilter.all.includes(try job("done")))
    }
    func testTaskSelectionDropsRevokedMembersAndLeader() throws {
        let members = try decode([Member].self, #"[{"id":"active","name":"A","role":"agent","permissions":{"execute":true}},{"id":"revoked","name":"R","role":"agent","permissions":{"execute":false}},{"id":"archived","name":"X","role":"agent","state":"archived"},{"id":"disabled","name":"D","role":"agent","enabled":false},{"id":"owner","name":"O","role":"human"}]"#)
        let valid = ComposerSelection(targets: ["active","revoked","archived","disabled","owner","missing"], leader: "revoked", members: members)
        XCTAssertEqual(valid.targets, ["active"]); XCTAssertNil(valid.leader)
        XCTAssertEqual(ComposerSelection(targets: ["active"], leader: "active", members: members).leader, "active")
    }
    func testHTTPSAndPrefixes() throws {
        let endpoint = try Endpoint(" https://example.com/chat/ ")
        XCTAssertEqual(endpoint.url("members").absoluteString, "https://example.com/chat/api/members")
        XCTAssertEqual(endpoint.notificationsURL.absoluteString, "wss://example.com/chat/api/notifications")
        XCTAssertEqual(try Endpoint("https://example.com").url("members").path, "/api/members")
        for value in ["http://example.com", "http://127.0.0.1", "https://user:password@example.com", "https://example.com?a=1", "https://example.com#fragment", "https://example.com/../path", "file:///tmp/file"] { XCTAssertThrowsError(try Endpoint(value)) }
        XCTAssertNoThrow(try Endpoint("http://127.0.0.1:9999", allowLoopbackHTTP: true))
        let url = endpoint.url("messages", query: [.init(name: "conversation_id", value: "会话 A&B?")])
        XCTAssertEqual(URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first?.value, "会话 A&B?")
    }
    func testPlainMentionsNeverDispatch() throws {
        let payload = try OutgoingMessage(text: "@codex-fixture-01 @所有人 讨论", conversationID: "A", dispatch: false, targets: ["codex-fixture-01"])
        let value = try JSONSerialization.jsonObject(with: JSONEncoder().encode(payload)) as! [String: Any]
        XCTAssertEqual(value["broadcast"] as? Bool, true)
        XCTAssertEqual(value["dispatch"] as? Bool, false)
        XCTAssertEqual(value["targets"] as? [String], [])
        XCTAssertNil(value["leader"])
        XCTAssertEqual(value["conversation_id"] as? String, "A")
    }
    func testDispatchFreezesKeyAndParticipants() throws {
        let original = try OutgoingMessage(text: "执行", conversationID: "A", dispatch: true, targets: ["test-cloud-01", "codex-fixture-01", "codex-fixture-01"], leader: "codex-fixture-01", replyTo: 7)
        let restored = try JSONDecoder().decode(OutgoingMessage.self, from: JSONEncoder().encode(original))
        XCTAssertEqual(original, restored); XCTAssertEqual(original.targets, ["codex-fixture-01", "test-cloud-01"])
        XCTAssertFalse(original.broadcast); XCTAssertEqual(original.replyTo, 7)
        XCTAssertThrowsError(try OutgoingMessage(text: "执行", conversationID: "A", dispatch: true))
        XCTAssertThrowsError(try OutgoingMessage(text: "执行", conversationID: "A", dispatch: true, targets: ["codex-fixture-01"], leader: "test-cloud-01"))
        XCTAssertThrowsError(try OutgoingMessage(text: " ", conversationID: "A", dispatch: false))
    }
    func testMessageMergeIsIsolatedAndUnique() throws {
        let a = try decode(Message.self, "{\"id\":1,\"author\":\"owner\",\"text\":\"A\",\"created\":1,\"conversation_id\":\"A\",\"targets\":[],\"attachments\":[]}")
        let b = try decode(Message.self, "{\"id\":2,\"author\":\"owner\",\"text\":\"B\",\"created\":2,\"conversation_id\":\"B\",\"targets\":[],\"attachments\":[]}")
        XCTAssertEqual(MessageMerge.merge([a], [a, b], conversationID: "A"), [a])
        XCTAssertEqual(MessageMerge.merge([a], [b], conversationID: "B"), [b])
    }
    func testProcessingAndCompletionAreNotAssumed() throws {
        let prefix = "{\"id\":1,\"message_id\":7,\"target\":\"codex-fixture-01\",\"attempts\":1,\"conversation_id\":\"A\","
        XCTAssertEqual(try decode(Job.self, prefix + "\"status\":\"running\",\"events\":[]}").stateLabel, "已领取")
        XCTAssertEqual(try decode(Job.self, prefix + "\"status\":\"running\",\"events\":[{\"state\":\"processing\",\"created\":1}]}").stateLabel, "处理中")
        XCTAssertEqual(try decode(Job.self, prefix + "\"status\":\"done\",\"completed\":0,\"events\":[]}").stateLabel, "阶段已返回")
        XCTAssertEqual(try decode(Job.self, prefix + "\"status\":\"done\",\"completed\":1,\"events\":[]}").stateLabel, "完成")
        XCTAssertEqual(try decode(Job.self, prefix + "\"status\":\"done\",\"blocked\":1,\"events\":[]}").stateLabel, "等待补充")
    }
    func testMentionsRespectCaretAndUnicode() throws {
        let text = "你好 @日 后面的文字"
        let match = try XCTUnwrap(Mentions.match(text: text, caret: ("你好 @日" as NSString).length))
        XCTAssertEqual(match.query, "日")
        XCTAssertEqual(Mentions.insert(text: text, match: match, name: "示例执行者").text, "你好 @示例执行者  后面的文字")
        XCTAssertNil(Mentions.match(text: "mail@example", caret: 12))
        XCTAssertNil(Mentions.match(text: "@Japan ", caret: 7))
        XCTAssertNotNil(Mentions.match(text: "👋 @", caret: ("👋 @" as NSString).length))
    }
    func testDeviceDefaultsAndAdminPermission() throws {
        let payload = try DeviceInviteRequest(name: "  工作室 Mac  ", type: .mac)
        XCTAssertEqual(payload.name, "工作室 Mac")
        XCTAssertEqual(payload.permissions, DevicePermissions())
        let encoded = try JSONEncoder().encode(payload)
        let body = try JSONSerialization.jsonObject(with: encoded) as! [String: Any]
        XCTAssertEqual(body["type"] as? String, "codex-macos")
        XCTAssertEqual(body["permissions"] as? [String: Bool], ["read": true, "execute": false, "delegate": false])
        XCTAssertNil(body["pairing_code"]); XCTAssertNil(body["token"])
        XCTAssertThrowsError(try DeviceInviteRequest(name: " ", type: .mac))
        XCTAssertThrowsError(try DeviceInviteRequest(name: "bad\nname", type: .linux))
        XCTAssertThrowsError(try DeviceInviteRequest(name: "unsafe", type: .muse, permissions: .init(delegate: true)))
        let admin = try decode(MembersResponse.self, "{\"me\":\"owner\",\"readonly\":false,\"members\":[{\"id\":\"owner\",\"name\":\"Owner\",\"role\":\"human\"}]}")
        XCTAssertTrue(admin.canManageDevices)
        let readonly = try decode(MembersResponse.self, "{\"me\":\"owner\",\"readonly\":true,\"members\":[]}")
        XCTAssertFalse(readonly.canManageDevices)
    }
    func testMemberSettingsRevisionAndPermissionValidation() throws {
        let json = #"{"id":"fixture-agent","name":"Agent","type":"muse","machine":"","tags":[],"permissions":{"read":true,"execute":false,"delegate":false},"state":"disabled","status":"disabled","legacy":true,"generation":3}"#
        let device = try decode(Device.self, json)
        XCTAssertEqual(device.statusLabel, "未启用")
        let enable = try DeviceSettingsRequest(device: device)
        let encoded = try JSONSerialization.jsonObject(with: JSONEncoder().encode(enable)) as! [String: Any]
        XCTAssertEqual(encoded["generation"] as? Int, 3); XCTAssertEqual(encoded["action"] as? String, "enable")
        XCTAssertNil(encoded["permissions"]); XCTAssertNil(encoded["token"])
        let settings = try DeviceSettingsRequest(device: device, permissions: .init(execute: true))
        XCTAssertEqual(settings.action, "permissions"); XCTAssertEqual(settings.generation, 3)
        XCTAssertThrowsError(try DeviceSettingsRequest(device: device, permissions: .init(delegate: true)))
        let old = try decode(Device.self, json.replacingOccurrences(of: ",\"generation\":3", with: ""))
        XCTAssertThrowsError(try DeviceSettingsRequest(device: old))
        let expired = try decode(DeviceInvitation.self, #"{"device_id":"fixture-agent","invitation_id":"public-id","pairing_code":"fixture-only","expires":1,"secret_available":true}"#)
        XCTAssertFalse(expired.hasValidCode)
    }
    func testMusePromptContainsOnlyPublicCloudMetadata() throws {
        let prompt = MuseEnrollment.prompt(name: "Cloud Muse", deviceID: "muse-device-fixture", invitationID: "public-invitation-fixture", address: "https://example.com/chat", permissions: .init())
        XCTAssertTrue(prompt.contains("Muse 云端账号"))
        XCTAssertTrue(prompt.contains("无需用户在自己的电脑或 iPhone 上运行程序"))
        XCTAssertTrue(prompt.contains("https://example.com/chat"))
        XCTAssertTrue(prompt.contains("muse-device-fixture"))
        XCTAssertTrue(prompt.contains("public-invitation-fixture"))
        XCTAssertTrue(prompt.contains("安全凭证入口"))
        XCTAssertTrue(prompt.contains("上传不代表已安装"))
        XCTAssertTrue(prompt.contains("不领取任务"))
        XCTAssertFalse(prompt.contains("python3 ai-group-connect.pyz"))
        XCTAssertFalse(prompt.contains("\"token\":")); XCTAssertFalse(prompt.contains("\"pairing_code\":"))
        let noInvitation = MuseEnrollment.prompt(name: "Already connected", deviceID: "muse-device-fixture", invitationID: nil, address: "https://example.com/chat", permissions: .init(execute: true))
        XCTAssertTrue(noInvitation.contains("\"invitation_id\" : null"))
    }
    func testExecutableMuseEnrollmentStartsTheTaskLoop() throws {
        let prompt = MuseEnrollment.prompt(name: "Task Muse", deviceID: "muse-device-test", invitationID: nil, address: "https://example.com/chat", permissions: .init(execute: true))
        for step in ["立即 POST /api/jobs/claim", "/api/jobs/progress", "/api/jobs/renew", "/api/jobs/finish", "每分钟", "每 15 秒", "409", "原 job_id", "具体缺少的能力"] {
            XCTAssertTrue(prompt.contains(step), step)
        }
        XCTAssertFalse(prompt.contains("本次只做接入验证"))
        let readonly = MuseEnrollment.prompt(name: "Reader", deviceID: "muse-device-test", invitationID: nil, address: "https://example.com/chat", permissions: .init())
        XCTAssertTrue(readonly.contains("不领取任务"))
        XCTAssertFalse(readonly.contains("立即 POST /api/jobs/claim"))
    }
    func testCloudCredentialIsSeparateFromPairingAndDisplayExpiry() throws {
        let request = CloudCredentialRequest(deviceID: "muse-device-fixture", generation: 4, key: "fixed-cloud-key")
        let body = try JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as! [String: Any]
        XCTAssertEqual(body["confirm"] as? Bool, true); XCTAssertEqual(body["generation"] as? Int, 4)
        XCTAssertNil(body["token"]); XCTAssertNil(body["pairing_code"])
        let json = "{\"member_id\":\"muse-device-fixture\",\"token\":\"fixture-only\",\"permissions\":{\"read\":true,\"execute\":false,\"delegate\":false},\"secret_available\":true,\"display_until\":" + String(Date().timeIntervalSince1970 + 300) + "}"
        var credential = try decode(CloudCredential.self, json)
        XCTAssertTrue(credential.canCopy); credential.token = nil; XCTAssertFalse(credential.canCopy)
        let replay = try decode(CloudCredential.self, #"{"member_id":"muse-device-fixture","permissions":{"read":true,"execute":false,"delegate":false},"secret_available":false,"notice":"Cannot retrieve old key"}"#)
        XCTAssertFalse(replay.canCopy); XCTAssertNil(replay.token)
        let prompt = MuseEnrollment.prompt(name: "Muse", deviceID: request.deviceID, invitationID: nil, address: "https://example.com/chat", permissions: .init())
        for instruction in ["默认流程不要调用 POST /api/devices/pair", "后台无法再次显示", "401 表示凭证不匹配", "不得“未保存 token”后宣称配对完成", "成员连接密钥不是一次性配对码"] { XCTAssertTrue(prompt.contains(instruction)) }
        XCTAssertFalse(prompt.contains("fixture-only"))
    }
    func testTransientNetworkErrorsUseConnectionStatus() {
        for code: URLError.Code in [.networkConnectionLost, .timedOut, .notConnectedToInternet, .cannotFindHost, .cannotConnectToHost, .dnsLookupFailed, .dataNotAllowed] {
            XCTAssertTrue(FailurePresentation.isConnectivity(URLError(code)))
        }
        for status in [408, 429, 500, 502, 503, 504] { XCTAssertTrue(FailurePresentation.isConnectivity(ClientError.server(status, "temporary"))) }
        for error: Error in [URLError(.serverCertificateUntrusted), ClientError.server(401, "login"), ClientError.server(403, "permission"), ClientError.validation("name")] { XCTAssertFalse(FailurePresentation.isConnectivity(error)) }
        XCTAssertTrue(FailurePresentation.isCancellation(URLError(.cancelled)))
        XCTAssertTrue(FailurePresentation.isCancellation(CancellationError()))
        XCTAssertFalse(FailurePresentation.isConnectivity(URLError(.cancelled)))
    }
    func testConnectionRequiresHTTPAndNotificationRecovery() {
        var health = ConnectionHealth(); health.foreground = true; health.authenticated = true
        XCTAssertEqual(health.label, "连接中"); XCTAssertNotNil(health.notice)
        health.notificationsReady = true
        XCTAssertEqual(health.label, "实时连接"); XCTAssertNil(health.notice)
        health.httpUnavailable = true
        XCTAssertEqual(health.label, "连接中"); XCTAssertNotNil(health.notice)
        health.httpUnavailable = false
        XCTAssertEqual(health.label, "实时连接"); XCTAssertNil(health.notice)
        health.notificationsReady = false
        XCTAssertEqual(health.label, "连接中")
        health.foreground = false
        XCTAssertEqual(health.label, "后台暂停"); XCTAssertNil(health.notice)
        health.authenticated = false; health.loginExpired = true
        XCTAssertEqual(health.label, "登录已失效")
    }
}
final class IntegrationTests: XCTestCase {
    func api(_ token: String = "ios-owner-fixture") throws -> RoomAPI {
        guard let value = ProcessInfo.processInfo.environment["AI_GROUP_TEST_URL"] else { throw XCTSkip("Start scripts/fixture.py for isolated HTTP integration") }
        return RoomAPI(endpoint: try Endpoint(value, allowLoopbackHTTP: true), token: token)
    }
    func testDeviceEnrollmentLifecycleAndPermissions() async throws {
        let api = try api(); defer { api.close() }
        let request = try DeviceInviteRequest(name: "iPhone invite " + UUID().uuidString.prefix(8), type: .mac)
        let invitation = try await api.inviteDevice(request)
        XCTAssertTrue(invitation.hasValidCode)
        let code = try XCTUnwrap(invitation.pairingCode)
        let replay = try await api.inviteDevice(request)
        XCTAssertEqual(replay.deviceID, invitation.deviceID)
        XCTAssertFalse(replay.secretAvailable); XCTAssertNil(replay.pairingCode)
        let list = try await api.devices()
        let device = try XCTUnwrap(list.devices.first { $0.id == invitation.deviceID })
        XCTAssertEqual(list.devices.filter { $0.id == device.id }.count, 1)
        XCTAssertEqual(device.statusLabel, "等待配对"); XCTAssertFalse(device.permissions.execute)
        // Bundle URL uses HTTPS even though the isolated transport is loopback HTTP.
        let host = api.endpoint.baseURL.absoluteString.replacingOccurrences(of: "http://", with: "https://")
        let bundle = try await api.raw("devices/bundle", method: "POST", body: JSONEncoder().encode(DeviceBundleRequest(deviceID: device.id, invitationID: invitation.invitationID, url: host)))
        XCTAssertEqual(Array(bundle.prefix(2)), [0x50, 0x4b])
        XCTAssertFalse(bundle.range(of: Data(code.utf8)) != nil)
        let renewed = try await api.inviteDevice(.init(reinvite: device.id))
        XCTAssertNotEqual(renewed.invitationID, invitation.invitationID)
        let oldBody = try JSONSerialization.data(withJSONObject: ["device_id": device.id, "invitation_id": invitation.invitationID, "pairing_code": code, "type": "codex-macos", "protocol": 1])
        do { let _: EmptyResponse = try await api.request("devices/pair", method: "POST", body: oldBody); XCTFail("old code must fail") }
        catch let error as ClientError { if case .server(let status, _) = error { XCTAssertEqual(status, 401) } else { throw error } }
        _ = try await api.deviceAction(.init(deviceID: device.id, archive: false))
        let revoked = try await api.devices(); XCTAssertEqual(revoked.devices.first { $0.id == device.id }?.state, "revoked")
        _ = try await api.deviceAction(.init(deviceID: device.id, archive: true))
        let archived = try await api.devices(); XCTAssertEqual(archived.devices.first { $0.id == device.id }?.state, "archived")
        for token in ["ios-read-fixture", "ios-codex-fixture"] {
            let denied = try self.api(token); defer { denied.close() }
            do { _ = try await denied.inviteDevice(request); XCTFail("must reject non-admin") }
            catch let error as ClientError { if case .server(let status, _) = error { XCTAssertEqual(status, 403) } else { throw error } }
            do { _ = try await denied.devices(); XCTFail("must reject list") }
            catch let error as ClientError { if case .server(let status, _) = error { XCTAssertEqual(status, 403) } else { throw error } }
        }
    }
    func testAgentArchiveNameReuseAndDelete() async throws {
        let api = try api(); defer { api.close() }
        let name = "Reusable " + UUID().uuidString
        let first = try await api.inviteDevice(DeviceInviteRequest(name: name, type: .mac))
        _ = try await api.deviceAction(.init(deviceID: first.deviceID, action: .archive))
        let next = try await api.inviteDevice(DeviceInviteRequest(name: name, type: .muse))
        XCTAssertNotEqual(first.deviceID, next.deviceID)
        _ = try await api.deviceAction(.init(deviceID: first.deviceID, action: .delete))
        _ = try await api.deviceAction(.init(deviceID: first.deviceID, action: .delete))
        let devices = try await api.devices()
        XCTAssertFalse(devices.devices.contains { $0.id == first.deviceID })
        XCTAssertTrue(devices.devices.contains { $0.id == next.deviceID })
        let members = try await api.members()
        let historical = try XCTUnwrap(members.members.first { $0.id == first.deviceID })
        XCTAssertTrue(historical.isRetired); XCTAssertFalse(historical.canExecute)
        XCTAssertEqual(historical.name, name)
        let denied = try self.api("ios-codex-fixture"); defer { denied.close() }
        do { _ = try await denied.deviceAction(.init(deviceID: next.deviceID, action: .delete)); XCTFail("only admin may delete") }
        catch let error as ClientError { if case .server(let status, _) = error { XCTAssertEqual(status, 403) } else { throw error } }
    }
    func testConversationMembersRoundTrip() async throws {
        let api = try self.api()
        let id = UUID().uuidString
        let created = try await api.saveConversation(id: id, title: "Scoped team", participants: ["test-cloud-01", "codex-fixture-01"])
        XCTAssertEqual(created.conversation.participants, ["codex-fixture-01", "test-cloud-01"])
        let duplicate = try await api.saveConversation(id: id, title: "Scoped team", participants: ["codex-fixture-01", "test-cloud-01"])
        XCTAssertEqual(duplicate.conversation.id, id)
        let metadata = try await api.messages(conversationID: id)
        XCTAssertEqual(metadata.conversation?.participants, ["codex-fixture-01", "test-cloud-01"])
        let renamed = try await api.saveConversation(id: id, title: "Renamed team", rename: true)
        XCTAssertEqual(renamed.conversation.participants, ["codex-fixture-01", "test-cloud-01"])
        let jobs = try await api.jobs(conversationID: id)
        XCTAssertTrue(jobs.jobs.isEmpty)
    }
    func testRealServerContract() async throws {
        let api = try api(); defer { api.close() }
        let members = try await api.members(); XCTAssertTrue(members.canWrite); XCTAssertEqual(members.me, "owner")
        let a = try await api.saveConversation(title: "iPhone A").conversation
        let b = try await api.saveConversation(title: "iPhone B").conversation
        let renamed = try await api.saveConversation(id: a.id, title: "iPhone A renamed", rename: true).conversation
        XCTAssertEqual(renamed.title, "iPhone A renamed")
        let payload = try OutgoingMessage(text: "@codex-fixture-01 普通聊天", conversationID: a.id, dispatch: false)
        let first = try await api.send(payload), again = try await api.send(payload)
        XCTAssertEqual(first.message.id, again.message.id); XCTAssertTrue(first.receipt.jobs.isEmpty)
        let allA = try await api.messages(conversationID: a.id)
        let allB = try await api.messages(conversationID: b.id)
        XCTAssertEqual(allA.messages.count, 1); XCTAssertTrue(allB.messages.isEmpty)
        let dispatch = try OutgoingMessage(text: "明确执行", conversationID: a.id, dispatch: true, targets: ["codex-fixture-01", "test-cloud-01"], leader: "codex-fixture-01")
        let receipt = try await api.send(dispatch)
        XCTAssertEqual(receipt.receipt.text, "收到"); XCTAssertEqual(receipt.receipt.jobs.map(\.target), ["codex-fixture-01"])
        let jobs = try await api.jobs(conversationID: a.id); XCTAssertEqual(jobs.jobs.count, 1); XCTAssertEqual(jobs.jobs[0].status, "pending")
        let file = try await api.upload(data: Data(repeating: 65, count: 600000), name: "fixture.txt", conversationID: b.id)
        let withFile = try OutgoingMessage(text: "附件", conversationID: b.id, dispatch: false, attachments: [file.id])
        let attached = try await api.send(withFile); XCTAssertEqual(attached.message.attachments.first?.size, 600000)
        let downloaded = try await api.download(file); XCTAssertEqual(downloaded, Data(repeating: 65, count: 600000))
        let older = try await api.messages(conversationID: a.id, before: receipt.message.id); XCTAssertEqual(older.messages.map(\.id), [first.message.id])
        let newer = try await api.messages(conversationID: a.id, after: first.message.id); XCTAssertEqual(newer.messages.map(\.id), [receipt.message.id])
    }
    func testRealNotificationChannel() async throws {
        #if os(Linux)
        throw XCTSkip("Linux URLSession does not implement ws/wss; run this test on macOS with the local fixture")
        #else
        let api = try api(); defer { api.close() }
        let socket = api.notifications(); socket.resume()
        let timeout = Task { try? await Task.sleep(nanoseconds: 5_000_000_000); if !Task.isCancelled { socket.cancel(with: .goingAway, reason: nil) } }
        defer { timeout.cancel(); socket.cancel(with: .goingAway, reason: nil) }
        try await api.authenticateNotifications(socket)
        let initial = try await socket.receive()
        if case .string(let body) = initial { XCTAssertTrue(body.contains("state")) } else { XCTFail("Expected state") }
        let _ = try await api.send(OutgoingMessage(text: "notification fixture", conversationID: "main", dispatch: false))
        let changed = try await socket.receive()
        if case .string(let body) = changed { XCTAssertTrue(body.contains("state")) } else { XCTFail("Expected state") }
        #endif
    }
    func testUnauthorizedAndReadonly() async throws {
        let wrong = try api("wrong"); defer { wrong.close() }
        do { let _ = try await wrong.members(); XCTFail("must reject") } catch let e as ClientError { XCTAssertTrue(e.isUnauthorized) }
        let readonly = try api("ios-read-fixture"); defer { readonly.close() }
        let me = try await readonly.members(); XCTAssertFalse(me.canWrite)
        do { let _ = try await readonly.send(OutgoingMessage(text: "blocked", conversationID: "main", dispatch: false)); XCTFail("must reject") } catch let e as ClientError { if case .server(let status, _) = e { XCTAssertEqual(status, 403) } else { XCTFail("unexpected error") } }
    }
}
