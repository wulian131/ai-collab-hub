import SwiftUI

@MainActor struct RootView: View {
    @AppStorage("accentChoice") private var accentChoice = "mint"
    private var accent: Color { AppPalette.color(accentChoice) }
    @EnvironmentObject var store: RoomStore
    @Environment(\.scenePhase) var phase
    @AppStorage("appearance") private var appearance = "system"
    var body: some View {
        Group {
            if store.isConnected { TabView {
                ConversationsView().tabItem { Label("对话", systemImage: "bubble.left.and.bubble.right.fill") }
                NavigationStack { TasksView() }.tabItem { Label("任务", systemImage: "checklist") }
                NavigationStack { MembersView() }.tabItem { Label("成员", systemImage: "person.2.fill") }
                NavigationStack { SettingsView() }.tabItem { Label("设置", systemImage: "gearshape.fill") }
            } } else { LoginView() }
        }.environment(\.roomAccent, accent).tint(accent).preferredColorScheme(appearance == "light" ? .light : appearance == "dark" ? .dark : nil)
            .task { store.setForeground(phase == .active); await store.restore() }
            .onChange(of: phase) { _, value in store.setForeground(value == .active) }
            .safeAreaInset(edge: .top, spacing: 0) {
                VStack(spacing: 0) {
                    if let notice = store.connectionNotice {
                        HStack(spacing: 8) { if store.isConnected { ProgressView().controlSize(.small) } else { Image(systemName: "wifi.slash") }; Text(notice).font(.caption); Spacer() }
                            .padding(.horizontal, 16).padding(.vertical, 9).background(Color(.secondarySystemBackground))
                            .accessibilityElement(children: .combine)
                    }
                    if let error = store.error {
                        HStack(spacing: 10) { Image(systemName: "exclamationmark.circle"); Text(error).font(.caption); Spacer(); Button { store.error = nil } label: { Image(systemName: "xmark") }.accessibilityLabel("关闭提示") }
                            .foregroundStyle(.secondary).padding(.horizontal, 16).padding(.vertical, 9).background(Color(.secondarySystemBackground))
                    }
                }
            }
    }
}
@MainActor struct LoginView: View {
    @Environment(\.roomAccent) private var accent
    @EnvironmentObject var store: RoomStore
    @State private var address = ""
    @State private var token = ""
    @FocusState private var focused: LoginField?
    private enum LoginField { case address, token }
    private func connect() { guard !store.loading else { return }; focused = nil; Task { do { try await store.connect(address: address, token: token); token = "" } catch { store.report(error) } } }
    var body: some View {
        NavigationStack { ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                VStack(alignment: .leading, spacing: 16) { Image(systemName: "bubble.left.and.bubble.right.fill").font(.system(size: 40)).foregroundStyle(accent).padding(18).background(accent.opacity(0.08), in: RoundedRectangle(cornerRadius: 24)); Text("AI 协作").font(.largeTitle.weight(.bold)); Text("让对话与任务，一起向前。").font(.title3).foregroundStyle(.secondary) }.padding(.top, 40)
                if store.health.loginExpired {
                    VStack(alignment: .leading, spacing: 10) {
                        Label("更新凭证，恢复连接", systemImage: "key.fill").font(.headline)
                        Text("服务地址已保留。粘贴新的登录凭证，验证为同一成员后恢复草稿与未确认发送；不会自动重发任务。").font(.callout)
                        if let member = store.recoveryMemberName { Text("之前的成员：" + member).font(.caption).foregroundStyle(.secondary) }
                        Text("Muse 密钥记错或丢失：请在另一台已登录的管理员设备进入该成员设置，选择“恢复连接 / 重新生成密钥”，再更新 Muse 的安全凭证库。管理员登录凭证丢失需由服务器管理员恢复，不能使用成员密钥代替。").font(.caption).foregroundStyle(.secondary)
                    }.padding(16).background(accent.opacity(0.08), in: RoundedRectangle(cornerRadius: 16))
                }
                VStack(alignment: .leading, spacing: 18) {
                    VStack(alignment: .leading, spacing: 8) { Text("服务地址").font(.subheadline.weight(.semibold)); TextField("https://chat.example.com/chat", text: $address).textContentType(.URL).keyboardType(.URL).focused($focused, equals: .address).submitLabel(.next).onSubmit { focused = .token }.textInputAutocapitalization(.never).autocorrectionDisabled().padding(14).background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12)) }
                    VStack(alignment: .leading, spacing: 8) { Text("登录凭证").font(.subheadline.weight(.semibold)); SecureField("粘贴你的用户登录凭证", text: $token).textContentType(.password).focused($focused, equals: .token).submitLabel(.go).onSubmit(connect).textInputAutocapitalization(.never).autocorrectionDisabled().padding(14).background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12)); Text("使用与网页登录相同的凭证。凭证仅保存在这台 iPhone 的钥匙串中。").font(.caption).foregroundStyle(.secondary) }
                    Button { connect() } label: { HStack { Spacer(); if store.loading { ProgressView().tint(Color(.systemBackground)) } else { Text("连接工作空间").font(.headline); Image(systemName: "arrow.right") }; Spacer() }.padding(16).background(accent, in: RoundedRectangle(cornerRadius: 14)).foregroundStyle(Color(.systemBackground)) }.disabled(store.loading || address.isEmpty || token.isEmpty)
                }
                Label("支持自己部署的服务", systemImage: "network").font(.caption).foregroundStyle(.secondary)
            }.padding(24).frame(maxWidth: 520).frame(maxWidth: .infinity)
        }.scrollDismissesKeyboard(.interactively).navigationBarTitleDisplayMode(.inline).onAppear { if address.isEmpty { address = store.address } } }
    }
}
@MainActor struct ConversationsView: View {
    @Environment(\.roomAccent) private var accent
    @EnvironmentObject var store: RoomStore
    @State private var search = ""
    @State private var creating = false
    @State private var path: [String] = []
    var body: some View {
        NavigationStack(path: $path) {
            List {
                Section { HStack(spacing: 7) { Circle().fill(store.connection == "实时连接" ? Color.green : Color.orange).frame(width: 6, height: 6); Text(store.connection).font(.caption); Spacer(); if let date = store.lastSync { Text(date, style: .time).font(.caption).foregroundStyle(.secondary) } }.listRowSeparator(.hidden) }
                ForEach(store.conversations) { conversation in Button { store.select(conversation.id); path = [conversation.id] } label: { HStack(alignment: .top, spacing: 12) { Image(systemName: "bubble.left.and.text.bubble.right").font(.title3).foregroundStyle(accent).frame(width: 42, height: 42).background(accent.opacity(0.07), in: RoundedRectangle(cornerRadius: 13)); VStack(alignment: .leading, spacing: 5) { HStack { Text(conversation.title).font(.headline).lineLimit(1).foregroundStyle(.primary); Spacer(); Text(Date(timeIntervalSince1970: conversation.updated), format: .dateTime.month(.abbreviated).day()).font(.caption2).foregroundStyle(.secondary) }; Text(conversation.summary?.isEmpty == false ? conversation.summary! : "开始一段新的对话").font(.subheadline).foregroundStyle(.secondary).lineLimit(2) } }.padding(.vertical, 12) }.listRowSeparator(.visible) }
                if store.conversationHasMore { Button("加载更多对话") { Task { await store.refreshConversations(search: search, more: true) } } }
            }.listStyle(.plain).navigationTitle("对话").searchable(text: $search, prompt: "搜索标题或消息")
                .navigationDestination(for: String.self) { id in ChatView(conversationID: id) }
                .toolbar { ToolbarItem(placement: .topBarTrailing) { Button { creating = true } label: { Image(systemName: "square.and.pencil") }.disabled(!store.canWrite || store.savingConversation).accessibilityLabel(store.pendingConversationTitle == nil ? "新建对话" : "重试新建对话") } }
                .refreshable { await store.refreshConversations(search: search); await store.refreshActive() }
                .task(id: search) { try? await Task.sleep(nanoseconds: 300_000_000); if !Task.isCancelled { await store.refreshConversations(search: search) } }
                .sheet(isPresented: $creating) {
                    NewConversationView { id in creating = false; search = ""; path = [id] }
                }
                .overlay { if store.conversations.isEmpty { EmptyState(title: search.isEmpty ? "你的工作空间" : "没有找到对话", detail: search.isEmpty ? "新建对话，开始协作。" : "试试其他关键词。") } }
        }
    }
}
@MainActor struct MembersView: View {
    @Environment(\.roomAccent) private var accent
    @EnvironmentObject var store: RoomStore
    @State private var search = ""
    private var visibleMembers: [Member] {
        store.members.filter { !$0.isRetired && (search.isEmpty || [$0.name, $0.id, $0.machine ?? ""].contains { $0.localizedCaseInsensitiveContains(search) }) }
    }
    var body: some View {
        List {
            if store.canManageDevices {
                Section {
                    NavigationLink { DevicesView(startAdding: true) } label: { Label("添加成员", systemImage: "person.badge.plus").foregroundStyle(accent) }
                    NavigationLink { DevicesView() } label: { Label("设备与接入管理", systemImage: "laptopcomputer.and.iphone") }
                }
            }
            Section("工作空间成员 · \(visibleMembers.count) 位") {
                if visibleMembers.isEmpty { Text("没有匹配的成员，试试名字或设备备注。").font(.subheadline).foregroundStyle(.secondary) }
                ForEach(visibleMembers) { member in
                    if store.canManageDevices && member.role == "agent" {
                        NavigationLink { DevicesView(memberID: member.id) } label: { memberRow(member) }
                            .accessibilityLabel("管理 " + member.name)
                    } else { memberRow(member) }
                }
            }
            if !store.canManageDevices { Section { Text("添加和管理成员需要管理员身份。当前身份可查看已有成员。").font(.caption).foregroundStyle(.secondary) } }
        }.listStyle(.insetGrouped).navigationTitle("成员").searchable(text: $search, prompt: "搜索成员或设备").task { await store.refreshMembers() }.refreshable { await store.refreshMembers() }
    }
    private func memberRow(_ member: Member) -> some View {
        HStack(spacing: 12) {
            Avatar(id: member.id, name: member.name, online: member.online)
            VStack(alignment: .leading, spacing: 4) {
                Text(member.name).font(.headline)
                Text(member.id + (member.role == "human" ? " · 用户" : " · AI 成员")).font(.caption).foregroundStyle(.secondary)
                if let activity = member.activity, !activity.isEmpty { Text(activity).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
            }
            Spacer()
            StatusPill(text: member.enabled == false ? "未启用" : member.online == true ? "在线" : "离线", color: member.enabled == false ? .secondary : member.online == true ? accent : .secondary)
        }.padding(.vertical, 5)
    }
}
@MainActor struct SettingsView: View {
    @EnvironmentObject var store: RoomStore
    @AppStorage("appearance") private var appearance = "system"
    @AppStorage("accentChoice") private var accentChoice = "mint"
    @State private var confirmLogout = false
    var body: some View {
        Form {
            Section("工作空间") { LabeledContent("服务地址", value: store.address); LabeledContent("当前身份", value: store.memberName(store.identity?.me ?? "")); LabeledContent("权限", value: store.canWrite ? "用户 · 聊天与派单" : "只读"); LabeledContent("连接", value: store.connection) }
            Section("外观") { Picker("主题", selection: $appearance) { Text("跟随系统").tag("system"); Text("白天").tag("light"); Text("深色").tag("dark") }; Picker("主题色", selection: $accentChoice) { Text("薄荷绿").tag("mint"); Text("晴空蓝").tag("blue"); Text("鸢尾紫").tag("violet") } }
            Section("消息与隐私") { DisclosureGroup("连接、草稿与隐私说明") { Text("前台通过实时通知更新；后台暂停连接，回来后补齐消息。首版未启用锁屏推送。"); Text("最近对话和草稿保存在本机受保护空间，退出时清除。未确认发送保留原标识，需手动重试。"); Text("本客户端仅连接您配置的聊天服务。") } }.font(.subheadline).foregroundStyle(.secondary)
            Section { LabeledContent("版本", value: "0.5.0 (9)").foregroundStyle(.secondary) }; Section { Button("退出登录", role: .destructive) { confirmLogout = true } }
        }.navigationTitle("设置").confirmationDialog("退出会清除本机登录、历史缓存、草稿和待重试消息。", isPresented: $confirmLogout, titleVisibility: .visible) { Button("退出并清除", role: .destructive) { store.logout() }; Button("取消", role: .cancel) {} }
    }
}

@MainActor private struct NewConversationView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.roomAccent) private var accent
    @EnvironmentObject var store: RoomStore
    let created: (String) -> Void
    @State private var title = ""
    @State private var search = ""
    @State private var selected: Set<String> = []
    private var frozen: Bool { store.pendingConversationTitle != nil }
    private var candidates: [Member] { store.roomCandidates.filter { search.isEmpty || $0.name.localizedCaseInsensitiveContains(search) || $0.id.localizedCaseInsensitiveContains(search) } }
    var body: some View {
        NavigationStack {
            List {
                Section("对话名称（可选）") { TextField("给这段协作起个名字", text: $title).disabled(frozen || store.savingConversation) }
                Section {
                    HStack { Button("全选") { if store.roomCandidates.count <= 20 { selected = Set(store.roomCandidates.map(\.id)) } else { store.error = "一次最多选择 20 位成员，请手动选择" } }; Spacer(); Button("清空") { selected = [] } }.disabled(frozen || store.savingConversation)
                    Text("已选择 \(selected.count) 位成员").font(.caption).foregroundStyle(.secondary)
                    Text("任务默认分配给所选成员中可执行的成员，也可用 @ 选择其中一部分。创建对话不会自动派发任务。").font(.caption).foregroundStyle(.secondary)
                }
                Section("对话成员") {
                    ForEach(candidates) { member in
                        Button {
                            if selected.contains(member.id) { selected.remove(member.id) }
                            else if selected.count < 20 { selected.insert(member.id) }
                            else { store.error = "一次最多选择 20 位成员" }
                        } label: {
                            HStack { Avatar(id: member.id, name: member.name, online: member.online); VStack(alignment: .leading) { Text(member.name).foregroundStyle(.primary); Text(member.canExecute ? "可执行任务" : "只读成员").font(.caption).foregroundStyle(.secondary) }; Spacer(); Image(systemName: selected.contains(member.id) ? "checkmark.circle.fill" : "circle").foregroundStyle(selected.contains(member.id) ? accent : .secondary) }.frame(minHeight: 44)
                        }.disabled(frozen || store.savingConversation)
                    }
                    if candidates.isEmpty { Text("没有匹配的成员").foregroundStyle(.secondary) }
                }
                if frozen { Section { Text("上次创建尚未确认，将重试相同的对话、标题和成员。").font(.caption).foregroundStyle(.secondary) } }
                if let error = store.error { Section { Text(error).font(.callout).foregroundStyle(.red) } }
            }.navigationTitle("新建对话").searchable(text: $search, prompt: "搜索成员")
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
                    ToolbarItem(placement: .confirmationAction) { Button(store.savingConversation ? "正在创建…" : frozen ? "重试原请求" : "创建") { Task { if let id = await store.saveConversation(title: title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "新对话" : title, participants: Array(selected)) { created(id) } } }.disabled(!store.canWrite || store.savingConversation || (!frozen && selected.isEmpty)) }
                }
                .onAppear { title = store.pendingConversationTitle ?? ""; selected = Set(store.pendingConversationParticipants ?? []) }
        }
    }
}
