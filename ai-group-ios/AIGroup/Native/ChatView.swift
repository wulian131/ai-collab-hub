import SwiftUI
import UIKit
import QuickLook

@MainActor struct ChatView: View {
    let conversationID: String
    @Environment(\.roomAccent) private var accent
    @EnvironmentObject var store: RoomStore
    @State private var attachmentURL: URL?
    @State private var showTasks = false
    @State private var showRename = false
    @State private var title = ""
    @State private var followLatest = true
    @State private var openingLatest = true
    @State private var viewportHeight: CGFloat = 0
    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 22) {
                    if store.messageHasMore { Button("加载更早消息") { openingLatest = false; followLatest = false; let first = store.messages.first?.id; Task { await store.earlierMessages(); await Task.yield(); if let first { proxy.scrollTo(first, anchor: .top) } } }.font(.caption).frame(maxWidth: .infinity) }
                    if store.messages.isEmpty { EmptyState(title: "开始这段对话", detail: "描述任务，输入 @ 选择执行成员；可指定负责人协调。") }
                    ForEach(Array(store.messages.enumerated()), id: \.element.id) { index, message in
                        if index == 0 || !Calendar.current.isDate(Date(timeIntervalSince1970: store.messages[index - 1].created), inSameDayAs: Date(timeIntervalSince1970: message.created)) {
                            Text(Date(timeIntervalSince1970: message.created), format: .dateTime.year().month().day()).font(.caption).foregroundStyle(.secondary).padding(.vertical, 8).frame(maxWidth: .infinity)
                        }
                        MessageRow(message: message, onAttachment: openAttachment).id(message.id)
                    }
                    ForEach(store.pending.filter { $0.conversationID == store.selectedID }, id: \.key) { payload in
                        VStack(alignment: .leading, spacing: 9) {
                            Text(payload.text).lineLimit(4)
                            Label(store.sending ? "正在发送，等待服务器确认" : "发送未确认 · 可用同一标识重试", systemImage: "clock").font(.caption).foregroundStyle(.secondary)
                            HStack { Button("重试") { Task { await store.retrySend(payload) } }.disabled(store.sending || !store.canWrite); Spacer(); Button("放弃重试", role: .destructive) { store.cancelPending(payload) } }.font(.caption)
                            Text("未确认的发送可能已经保存。重试会避免重复入队；放弃不会撤回已保存的消息。").font(.system(size: 10)).foregroundStyle(.secondary)
                        }.padding(14).background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14))
                    }
                    Color.clear.frame(height: 1).background(GeometryReader { geometry in Color.clear.preference(key: LatestBottomKey.self, value: geometry.frame(in: .named("messageScroll")).maxY) }).id("latest")
                }.padding(.horizontal, 16).padding(.vertical, 20)
            }.defaultScrollAnchor(openingLatest ? .bottom : nil).coordinateSpace(name: "messageScroll").background(GeometryReader { geometry in Color(.systemBackground).onAppear { viewportHeight = geometry.size.height }.onChange(of: geometry.size.height) { _, height in viewportHeight = height } }).scrollDismissesKeyboard(.interactively)
                .onPreferenceChange(LatestBottomKey.self) { bottom in
                    guard bottom != 0, viewportHeight > 0 else { return }
                    if openingLatest {
                        if !store.messages.isEmpty {
                            proxy.scrollTo("latest", anchor: .bottom)
                            if bottom <= viewportHeight + 5 { openingLatest = false }
                        }
                    } else { followLatest = bottom < viewportHeight + 110 }
                }
                .simultaneousGesture(DragGesture(minimumDistance: 2).onChanged { _ in openingLatest = false })
                .refreshable { openingLatest = false; followLatest = false; await store.refreshActive() }
                .onAppear { openingLatest = true; followLatest = true; store.select(conversationID); Task { await Task.yield(); if openingLatest { proxy.scrollTo("latest", anchor: .bottom) } } }
                .onChange(of: store.selectedID) { _, _ in openingLatest = true; followLatest = true; Task { await Task.yield(); if openingLatest { proxy.scrollTo("latest", anchor: .bottom) } } }
                .onChange(of: store.messages.last?.id) { _, _ in if followLatest || openingLatest { Task { await Task.yield(); if followLatest || openingLatest { proxy.scrollTo("latest", anchor: .bottom) } } } }
                .overlay(alignment: .bottomTrailing) { if !followLatest { Button { openingLatest = false; followLatest = true; withAnimation { proxy.scrollTo("latest", anchor: .bottom) } } label: { Label("最新消息", systemImage: "arrow.down").font(.caption).padding(10).background(.regularMaterial, in: Capsule()) }.padding() } }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) { VStack(spacing: 0) {
            if let receipt = store.receiptText { Text(receipt).font(.caption).foregroundStyle(accent).padding(8).frame(maxWidth: .infinity).background(accent.opacity(0.06)) }
            ComposerView()
        } }
        .navigationTitle(store.selectedConversation?.title ?? "对话").navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Menu {
            Button("任务进度", systemImage: "checklist") { showTasks = true }
            Button("重新同步", systemImage: "arrow.clockwise") { Task { await store.refreshActive() } }
            if store.canWrite { Button("重命名", systemImage: "pencil") { title = store.selectedConversation?.title ?? ""; showRename = true } }
        } label: { Image(systemName: "ellipsis.circle") } } }
        .sheet(isPresented: $showTasks) { NavigationStack { TasksView().toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { showTasks = false } } } } }
        .quickLookPreview($attachmentURL)
        .alert("重命名对话", isPresented: $showRename) { TextField("对话标题", text: $title); Button("保存") { Task { await store.saveConversation(title: title, rename: true) } }; Button("取消", role: .cancel) {} }
    }
    private func openAttachment(_ item: Attachment) { Task { do { attachmentURL = try await store.download(item) } catch { store.report(error) } } }
}
@MainActor struct MessageRow: View {
    @Environment(\.roomAccent) private var accent
    @EnvironmentObject var store: RoomStore
    let message: Message
    let onAttachment: (Attachment) -> Void
    @State private var expanded = false
    @State private var copied = false
    private var previewLimit: Int { message.author == "hub" ? 220 : 800 }
    private var needsExpansion: Bool { message.text.count > (message.author == "hub" ? 500 : 1400) }
    var mine: Bool { message.author == store.identity?.me }
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            if mine { Spacer(minLength: 44) }
            if !mine { Avatar(id: message.author, name: store.memberName(message.author)) }
            VStack(alignment: .leading, spacing: 7) {
                HStack(spacing: 6) { Text(mine ? "你" : store.memberName(message.author)).font(.caption.weight(.semibold)); Text(Date(timeIntervalSince1970: message.created), format: .dateTime.hour().minute()).font(.caption2).accessibilityLabel(timestamp(message.created)).foregroundStyle(.secondary); Spacer() }
                VStack(alignment: .leading, spacing: 10) {
                    if let replyID = message.replyTo { let parent = store.messages.first { $0.id == replyID }; VStack(alignment: .leading, spacing: 3) { Text("回复 #\(replyID) · " + (parent.map { store.memberName($0.author) } ?? "更早消息")).font(.caption.weight(.semibold)); Text(parent?.text ?? "加载更早消息可查看引用").font(.caption).foregroundStyle(.secondary).lineLimit(2) }.padding(9).frame(maxWidth: .infinity, alignment: .leading).background(accent.opacity(0.07), in: RoundedRectangle(cornerRadius: 8)) }
                    MarkdownBody(text: expanded || !needsExpansion ? message.text : String(message.text.prefix(previewLimit)) + "…")
                    if needsExpansion { Button(expanded ? "收起" : "展开完整消息") { expanded.toggle() }.font(.caption) }
                    if !message.targets.isEmpty { Text("→ " + message.targets.map(store.memberName).joined(separator: "、")).font(.caption).foregroundStyle(accent) }
                    ForEach(message.attachments) { item in Button { onAttachment(item) } label: { HStack { Image(systemName: "doc"); VStack(alignment: .leading) { Text(item.name).lineLimit(2); Text(ByteCountFormatter.string(fromByteCount: Int64(item.size), countStyle: .file)).font(.caption).foregroundStyle(.secondary) }; Spacer(); Image(systemName: "square.and.arrow.up") }.padding(10).background(Color(.tertiarySystemFill), in: RoundedRectangle(cornerRadius: 9)) } }
                }.padding(14).background(mine ? accent.opacity(0.09) : Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 17))
                    .contextMenu { Button("回复", systemImage: "arrowshape.turn.up.left") { store.reply = message }; Button("复制", systemImage: "doc.on.doc") { UIPasteboard.general.string = message.text; copied = true; Task { try? await Task.sleep(nanoseconds: 2_000_000_000); copied = false } } }
                HStack { Button { store.reply = message } label: { Label("回复", systemImage: "arrowshape.turn.up.left").font(.caption).frame(minWidth: 44, minHeight: 44) }.foregroundStyle(.secondary); if copied { Label("已复制", systemImage: "checkmark").font(.caption).foregroundStyle(accent) }; Spacer(); Text("#\(message.id)").font(.system(size: 10)).foregroundStyle(.tertiary) }
            }.frame(maxWidth: 640, alignment: .leading)
            if !mine { Spacer(minLength: 0) }
        }.frame(maxWidth: .infinity, alignment: mine ? .trailing : .leading)
    }
}
private struct LatestBottomKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = nextValue() }
}
