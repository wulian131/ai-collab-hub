import SwiftUI
import AppKit
@main struct ChatSmoke {
    @MainActor static func main() async throws {
        _ = NSApplication.shared
        var checks = 0
        func check(_ condition: @autoclosure () throws -> Bool, _ label: String) throws {
            guard try condition() else { throw ClientError.validation("FAIL " + label) }; checks += 1; print("PASS " + label)
        }
        let store = RoomStore()
        try await store.connect(address: "http://127.0.0.1:18917", token: "ios-owner-fixture")
        try check(store.isConnected && store.canWrite, "synthetic login and native state")
        let id = await store.saveConversation(title: "Mac desktop smoke", participants: ["codex-fixture-01","test-cloud-01"])
        try check(id != nil && store.selectedID == id, "conversation creation and member scope")
        _ = await store.saveConversation(title: "Renamed Mac smoke", rename: true)
        await store.refreshConversations(search: "Renamed Mac smoke")
        try check(store.conversations.contains { $0.id == id }, "conversation rename and search")
        await store.refreshActive()
        try check(store.defaultConversationTargets == ["codex-fixture-01","test-cloud-01"], "default participants and @ targets")
        await store.addFile(data: Data("synthetic attachment".utf8), name: "smoke.txt")
        try check(store.uploads.count == 1, "synthetic attachment upload")
        store.draft = "Native task with code\n```swift\nlet value = 1\n```"
        await store.send(targets: ["codex-fixture-01","test-cloud-01"], leader: "codex-fixture-01")
        try check(store.pending.isEmpty && store.jobs.first?.target == "codex-fixture-01", "task receipt and selected leader")
        let attachment = store.messages.last!.attachments[0]
        let file = try await store.download(attachment)
        try check(try String(contentsOf: file) == "synthetic attachment", "download round trip")
        let export = URL(fileURLWithPath: ProcessInfo.processInfo.environment["MAC_FIXTURE_DIRECTORY"]!).appendingPathComponent("native-export.txt")
        try Data(contentsOf: file).write(to: export, options: .atomic)
        try check(try Data(contentsOf: export) == Data("synthetic attachment".utf8), "native export bytes")
        store.draft = "simulate-lost-response"
        await store.send(targets: ["codex-fixture-01"], leader: nil)
        try check(store.pending.count == 1 && store.health.httpUnavailable, "uncertain write retained and nonmodal offline status")
        let pending = store.pending[0]
        store.draft = "同一身份的中文草稿"; store.saveDraft()
        store.report(ClientError.server(401,"synthetic auth expired"))
        try check(store.health.loginExpired && !store.isConnected && store.selectedID == id, "401 distinct with current conversation retained")
        try await store.connect(address: "http://127.0.0.1:18917", token: "ios-owner-replacement")
        try check(store.draft == "同一身份的中文草稿" && store.pending.first?.key == pending.key && store.selectedID == id, "same identity recovery preserves draft, room and frozen send")
        let api = RoomAPI(endpoint: try Endpoint("http://127.0.0.1:18917",allowLoopbackHTTP:true),token:"ios-owner-fixture")
        let before: [String:Int] = try await api.request("test/counters")
        await store.refreshActive()
        let after: [String:Int] = try await api.request("test/counters")
        try check(before["sends"] == after["sends"], "identity recovery never auto resends")
        await store.retrySend(pending)
        try check(store.pending.isEmpty && store.messages.filter { $0.key == pending.key }.count == 1, "manual frozen retry deduplicates")
        let model = DevicesModel(); model.bind(store)
        let request = try DeviceInviteRequest(name: "Synthetic Mac member", type: .muse, permissions: .init(execute:true))
        let invited = await model.invite(request)
        try check(invited && model.setup != nil, "native member enrollment")
        let issued = await model.issueCloudCredential()
        try check(issued && model.cloudCredential?.canCopy == true, "Muse separate cloud key")
        model.concealCode(); try check(model.cloudCredential?.token == nil, "conceal synthetic secret")
        DesktopPasteboard.copy("synthetic copied text")
        try check(NSPasteboard.general.string(forType:.string) == "synthetic copied text", "AppKit copy")
        try check(DesktopMessageTextView.shouldSend(keyCode:36,modifiers:[],marked:false), "Enter sends")
        try check(!DesktopMessageTextView.shouldSend(keyCode:36,modifiers:.shift,marked:false), "Shift Enter preserves newline")
        try check(!DesktopMessageTextView.shouldSend(keyCode:36,modifiers:[],marked:true), "IME marked selection never sends")
        let textView = DesktopMessageTextView(); textView.setMarkedText("中文",selectedRange:NSRange(location:2,length:0),replacementRange:NSRange(location:NSNotFound,length:0))
        try check(textView.hasMarkedText(), "NSTextView actual marked text")
        textView.unmarkText(); try check(!textView.hasMarkedText(), "NSTextView commit selection")
        api.close();store.setForeground(false)
        print("VERIFIED native chat smoke: \(checks) checks")
    }
}
