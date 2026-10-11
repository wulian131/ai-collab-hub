import SwiftUI
import UniformTypeIdentifiers


@MainActor struct ComposerView: View {
    @Environment(\.roomAccent) private var accent
    @EnvironmentObject var store: RoomStore
    @State private var targets: Set<String> = []
    @State private var usingRoomDefaults = true
    @State private var leader: String?
    @State private var caret = 0
    @State private var showTargets = false
    @State private var showFiles = false
    private var mention: MentionMatch? { Mentions.match(text: store.draft, caret: caret) }
    private var choices: [Member] {
        let list = store.conversationExecutors
        guard let match = mention else { return [] }
        return list.filter { match.query.isEmpty || $0.name.localizedCaseInsensitiveContains(match.query) || $0.id.localizedCaseInsensitiveContains(match.query) }
    }
    private func chooseMention(_ member: Member?) {
        guard let match = mention else { return }
        let inserted = Mentions.insert(text: store.draft, match: match, name: member?.name ?? "所有人")
        store.draft = inserted.text; caret = inserted.caret
        if usingRoomDefaults { targets = []; leader = nil; usingRoomDefaults = false }
        if let member { targets.insert(member.id) } else { targets.formUnion(store.conversationExecutors.map(\.id)) }
    }
    private var canSend: Bool { store.canWrite && !store.sending && !store.uploading && !targets.isEmpty && (!store.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !store.uploads.isEmpty) }
    private func send() { guard canSend else { return }; Task { await store.send(targets: Array(targets), leader: leader) } }
    var body: some View {
        VStack(spacing: 9) {
            if !store.sending, store.pending.contains(where: { $0.conversationID == store.selectedID }) {
                Label("有消息发送未确认，请在消息旁重试", systemImage: "exclamationmark.circle").font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
            }
            if let reply = store.reply {
                HStack { VStack(alignment: .leading, spacing: 3) { Text("回复 " + store.memberName(reply.author)).font(.caption).bold(); Text(reply.text).font(.caption).lineLimit(2).foregroundStyle(.secondary) }; Spacer(); Button { store.reply = nil } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary) }.accessibilityLabel("取消引用回复") }.padding(10).background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 10))
            }
            if let _ = mention, store.canWrite {
                ScrollView { VStack(spacing: 0) {
                    Button { chooseMention(nil) } label: { HStack { Image(systemName: "person.3.fill").frame(width: 32); Text("所有人"); Spacer() }.padding(11) }
                    ForEach(choices) { member in Button { chooseMention(member) } label: { HStack { Avatar(id: member.id, name: member.name, online: member.online); VStack(alignment: .leading) { Text(member.name); Text(member.id).font(.caption).foregroundStyle(.secondary) }; Spacer() }.padding(8) } }
                } }.frame(maxHeight: 220).background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12)).accessibilityLabel("选择提及成员")
            }
            if !store.uploads.isEmpty {
                ScrollView(.horizontal) { HStack { ForEach(store.uploads) { item in HStack { Image(systemName: "paperclip"); Text(item.name).lineLimit(1); Button { Task { await store.removeUpload(item) } } label: { Image(systemName: "xmark.circle.fill") } }.font(.caption).padding(8).background(Color(nsColor: .controlBackgroundColor), in: Capsule()) } } }
            }
            HStack(spacing: 12) {
                Button { showTargets = true } label: { Label(targets.isEmpty ? "执行成员" : "成员 \(targets.count)", systemImage: "person.2").font(.caption.weight(.semibold)).frame(minHeight: 44) }.disabled(!store.canWrite)
                Spacer()
                if store.uploading { ProgressView().controlSize(.small) }
                Menu {
                    Button("选择文件", systemImage: "doc") { showFiles = true }
                } label: { Image(systemName: "paperclip").font(.body).frame(minWidth: 44, minHeight: 44) }.accessibilityLabel("添加文件").disabled(!store.canWrite || store.uploading || store.sending)
            }
            Menu {
                    Picker("指定负责人", selection: $leader) {
                        Text("不指定负责人").tag(Optional<String>.none)
                        ForEach(store.conversationExecutors.filter { targets.contains($0.id) }) { member in Text(member.name).tag(Optional(member.id)) }
                    }
                } label: { HStack { Label("指定负责人", systemImage: "person.badge.key"); Spacer(); Text(leader.map { store.memberName($0) } ?? "不指定").lineLimit(1); Image(systemName: "chevron.down").font(.caption2) }.font(.caption).frame(minHeight: 44) }
                    .disabled(!store.canWrite || targets.isEmpty).accessibilityLabel("指定负责人")
            Group {
                HStack(alignment: .top) {
                    Text(targets.isEmpty ? store.conversationParticipants == nil ? "先选择执行成员，或输入 @ 选择。" : "本对话暂无可执行成员，请检查成员权限。" : store.conversationExecutors.filter { targets.contains($0.id) }.map(\.name).joined(separator: "、")).lineLimit(2)
                    Spacer()
                }.font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack(alignment: .bottom, spacing: 8) {
                ZStack(alignment: .topLeading) {
                    if store.draft.isEmpty { Text(store.canWrite ? "描述任务，@ 选择执行成员" : "当前身份仅可查看").foregroundStyle(.tertiary).padding(.top, 14).padding(.leading, 10).allowsHitTesting(false) }
                    MessageEditor(text: $store.draft, caret: $caret, onSend: send).frame(height: min(120, max(48, CGFloat(store.draft.components(separatedBy: "\n").count) * 22 + 26))).disabled(!store.canWrite)
                }.background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 15))
                Button(action: send) { Group { if store.sending { ProgressView().tint(Color(nsColor: .windowBackgroundColor)) } else { Image(systemName: "arrow.up").font(.system(size: 18, weight: .bold)) } }.frame(width: 44, height: 44).foregroundStyle(Color(nsColor: .windowBackgroundColor)).background(accent, in: Circle()) }
                    .disabled(!canSend).opacity(canSend ? 1 : 0.45).accessibilityLabel("分配任务")
            }
            Text("分配任务 · Enter 发送，Shift + Enter 换行").font(.caption2).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
        }.padding(.horizontal, 16).padding(.vertical, 10).background(.regularMaterial)
        .task(id: store.draft) { try? await Task.sleep(nanoseconds: 400_000_000); if !Task.isCancelled { store.saveDraft() } }
        .onAppear { if usingRoomDefaults { targets = store.defaultConversationTargets } }
        .onChange(of: store.conversationParticipants) { _, _ in targets = store.defaultConversationTargets; usingRoomDefaults = true; leader = nil }
        .onChange(of: store.selectedID) { _, _ in targets = store.defaultConversationTargets; usingRoomDefaults = true; leader = nil; caret = (store.draft as NSString).length }
        .onChange(of: store.conversationExecutors) { _, members in
            if usingRoomDefaults { targets = store.defaultConversationTargets }
            let valid = ComposerSelection(targets: targets, leader: leader, members: members)
            targets = valid.targets; leader = valid.leader
        }
        .sheet(isPresented: $showTargets) { targetPicker.frame(minWidth: 440, minHeight: 420) }
        .fileImporter(isPresented: $showFiles, allowedContentTypes: [.item]) { result in
            do { let url = try result.get(); let access = url.startAccessingSecurityScopedResource(); defer { if access { url.stopAccessingSecurityScopedResource() } }; let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0; guard size <= 10 * 1024 * 1024 else { throw ClientError.validation("单个附件最多 10 MB") }; let data = try Data(contentsOf: url); Task { await store.addFile(data: data, name: url.lastPathComponent) } } catch { store.report(error) }
        }
    }
    private var targetPicker: some View {
        NavigationStack { List {
            Section { Button("选择全部可执行成员") { usingRoomDefaults = false; targets = Set(store.conversationExecutors.map(\.id)) }; Button("清空选择") { usingRoomDefaults = false; targets = []; leader = nil } }
            Section("执行成员") { ForEach(store.conversationExecutors) { member in Button { usingRoomDefaults = false; if targets.contains(member.id) { targets.remove(member.id); if leader == member.id { leader = nil } } else { targets.insert(member.id) } } label: { HStack { Avatar(id: member.id, name: member.name, online: member.online); Text(member.name); Spacer(); Image(systemName: targets.contains(member.id) ? "checkmark.circle.fill" : "circle").foregroundStyle(targets.contains(member.id) ? accent : .secondary) } } } }
            Section("负责人") { Picker("协调本次任务", selection: $leader) { Text("不指定").tag(Optional<String>.none); ForEach(store.conversationExecutors.filter { targets.contains($0.id) }) { member in Text(member.name).tag(Optional(member.id)) } } }
        }.navigationTitle("分派给谁").toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { showTargets = false } } } }
    }
}
