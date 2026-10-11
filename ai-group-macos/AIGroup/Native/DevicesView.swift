import SwiftUI
import UniformTypeIdentifiers
import AppKit

struct ConnectionPackage: FileDocument {
    static var readableContentTypes: [UTType] { [.data] }
    var data: Data
    init(data: Data) { self.data = data }
    init(configuration: ReadConfiguration) throws { data = configuration.file.regularFileContents ?? Data() }
    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper { FileWrapper(regularFileWithContents: data) }
}

@MainActor struct DevicesView: View {
    @EnvironmentObject var store: RoomStore
    @Environment(\.scenePhase) private var phase
    @StateObject private var model: DevicesModel
    @State private var adding: Bool
    @State private var confirming: DeviceConfirmation?
    @State private var editing: Device?
    @State private var presenceDevice: Device?
    init(startAdding: Bool = false, memberID: String? = nil) {
        _adding = State(initialValue: startAdding)
        _model = StateObject(wrappedValue: DevicesModel(memberID: memberID))
    }
    var body: some View {
        Group {
            if !store.canManageDevices {
                EmptyState(title: "需要管理员身份", detail: "添加与管理设备仅对管理员开放。")
            } else if let setup = model.setup {
                ConnectMemberView(model: model, setup: setup)
            } else if adding {
                AddMemberView(model: model) { adding = false }
            } else {
                deviceList
            }
        }.navigationTitle(model.setup != nil ? "连接成员" : adding ? "添加成员" : model.selectedMemberID == nil ? "设备与接入" : (model.displayedDevices.first?.name ?? "成员设置"))
            
            .toolbar {
                if model.setup != nil || adding {
                    ToolbarItem(placement: .primaryAction) { Button(model.selectedMemberID == nil ? "返回列表" : "返回成员设置") { model.dismissSetup(); model.pendingRequest = nil; model.error = nil; adding = false; Task { await model.refresh() } }.disabled(model.busy) }
                } else if store.canManageDevices && model.selectedMemberID != nil {
                    ToolbarItem(placement: .primaryAction) { Button("刷新") { Task { await model.refresh() } }.disabled(model.busy) }
                } else if store.canManageDevices {
                    ToolbarItem(placement: .primaryAction) { Button { adding = true; model.error = nil } label: { Image(systemName: "person.badge.plus") }.accessibilityLabel("添加成员").disabled(model.busy || model.pendingRequest != nil || model.pendingSettings != nil || model.pendingCloudRequest != nil) }
                }
            }
            .task { model.bind(store); model.setForeground(phase == .active); await model.refresh() }
            .onChange(of: phase) { _, value in model.setForeground(value == .active) }
            .onChange(of: store.canManageDevices) { _, value in if !value { model.clear() } }
            .onDisappear { model.setForeground(false) }
            .sheet(item: $editing) { device in NavigationStack { MemberPermissionsView(model: model, device: device) } }
            .sheet(item: $presenceDevice) { device in MusePresenceView(device: device, address: store.address) }
            .alert(item: $confirming) { value in
                Alert(title: Text(value.title), message: Text(value.message), primaryButton: .destructive(Text(value.title)) {
                    Task {
                        if value.kind == .enable { await model.enable(value.device) }
                        else if value.kind == .rotate { _ = await model.invite(.init(reinvite: value.device.id), device: value.device) }
                        else { await model.action(value.device, action: value.kind == .archive ? .archive : value.kind == .delete ? .delete : .revoke) }
                    }
                }, secondaryButton: .cancel(Text("取消")))
            }
    }
    private var deviceList: some View {
        List {
            if model.selectedMemberID == nil { Section {
                Button { adding = true; model.error = nil } label: { Label("添加成员", systemImage: "person.badge.plus") }.disabled(model.busy || model.pendingRequest != nil || model.pendingSettings != nil || model.pendingCloudRequest != nil)
                Text("添加运行 AI 的设备或连接器。现有成员无需重复添加。").font(.caption).foregroundStyle(.secondary)
            } }
            if let error = model.error { Section { Text(error).foregroundStyle(.red).font(.callout) } }
            if let notice = model.notice { Section { Label(notice, systemImage: "checkmark.circle.fill").foregroundStyle(.green) } }
            if let pending = model.pendingSettings {
                Section("待确认设置") {
                    Button("用原设置重试") { Task { await model.configure(pending) } }.disabled(model.busy)
                    Button("停止重试并检查列表") { model.pendingSettings = nil; Task { await model.refresh() } }.disabled(model.busy)
                    Text("不会自动重复保存。停止重试不会撤销可能已保存的设置。").font(.caption).foregroundStyle(.secondary)
                }
            }
            if let pending = model.pendingCloudRequest {
                Section("待确认连接密钥") {
                    Button("用原请求确认结果") { if let d = model.devices.first(where: { $0.id == pending.deviceID }) { model.instructions(d); Task { await model.issueCloudCredential() } } }.disabled(model.busy)
                    Button("停止重试并检查列表") { model.pendingCloudRequest = nil; Task { await model.refresh() } }.disabled(model.busy)
                    Text("密钥不会在重试中重复显示。未保存时需明确生成新密钥。").font(.caption).foregroundStyle(.secondary)
                }
            }
            if let pending = model.pendingRequest {
                Section("待确认邀请") {
                    Button("用原请求重试") { Task { let device = model.devices.first { $0.id == pending.deviceID }; _ = await model.invite(pending, device: device) } }.disabled(model.busy)
                    Button("停止重试并检查列表") { model.pendingRequest = nil; Task { await model.refresh() } }.disabled(model.busy)
                    Text("停止重试不会撤销可能已创建的邀请。配对码不会再次返回，需要时可重发。").font(.caption).foregroundStyle(.secondary)
                }
            }
            Section(model.selectedMemberID == nil ? "接入设备" : "权限与接入") {
                if model.displayedDevices.isEmpty { Text((model.busy || (!model.didLoad && model.error == nil)) ? "正在读取…" : model.selectedMemberID == nil ? "还没有新增设备" : model.error == nil ? "该成员已删除或不可访问。" : "未能读取该成员，请重试。").foregroundStyle(.secondary) }
                ForEach(model.displayedDevices) { device in
                    VStack(alignment: .leading, spacing: 10) {
                        HStack { Label(device.name, systemImage: device.type == .muse ? "sparkles" : "desktopcomputer").font(.headline); Spacer(); Text(device.statusLabel).font(.caption).foregroundStyle(device.status == "connected" ? Color.green : Color.secondary) }
                        Text((device.isLegacy ? "已有 Agent" : device.type.label) + (device.machine.isEmpty ? "" : " · " + device.machine)).font(.caption).foregroundStyle(.secondary)
                        Text("权限：" + device.permissions.label).font(.caption).foregroundStyle(.secondary)
                        if let seen = device.lastSeen { Text("最后连接：" + Date(timeIntervalSince1970: seen).formatted()).font(.caption).foregroundStyle(.secondary) }
                        if device.type == .muse && device.state == "active" && device.status == "offline" {
                            Text("最近心跳已超时。请检查 Muse 云端心跳与检查入口；已启用不代表持续在线。").font(.caption).foregroundStyle(.secondary)
                        }
                        if device.state != "archived" {
                            HStack {
                                Button(device.state == "active" ? "已启用" : "启用") {
                                    if device.isLegacy || device.type == .muse { Task { await model.enable(device) } }
                                    else { confirming = .init(device: device, kind: .enable) }
                                }.disabled(device.state == "active" || model.busy || model.pendingRequest != nil || model.pendingSettings != nil || model.pendingCloudRequest != nil)
                                Button("修改权限") { model.error = nil; editing = device }.disabled(model.busy || model.pendingRequest != nil || model.pendingSettings != nil || model.pendingCloudRequest != nil)
                            }.buttonStyle(.borderless)
                        }
                        HStack {
                            if device.state != "archived" && device.type == .muse { Button("恢复连接 / 重新生成密钥") { model.instructions(device) }.buttonStyle(.borderless).disabled(model.busy || model.pendingCloudRequest != nil) }
                            else if device.state == "active" && !device.isLegacy { Button("连接说明") { model.instructions(device) }.buttonStyle(.borderless) }
                            Spacer()
                            Menu("管理", systemImage: "ellipsis.circle") {
                                if device.state != "archived" {
                                    if !device.isLegacy {
                                        if device.type == .muse { Button("重新生成连接密钥") { model.instructions(device) } }
                                        else { Button("重发邀请 / 轮换凭证") { confirming = .init(device: device, kind: .rotate) } }
                                        if device.state != "revoked" { Button("撤销接入", role: .destructive) { confirming = .init(device: device, kind: .revoke) } }
                                    }
                                    if device.type == .muse { Button("保持在线提示词") { presenceDevice = device } }
                                    Button("归档", role: .destructive) { confirming = .init(device: device, kind: .archive) }
                                }
                                Button("删除 Agent", role: .destructive) { confirming = .init(device: device, kind: .delete) }
                            }.disabled(model.busy || model.pendingRequest != nil || model.pendingSettings != nil || model.pendingCloudRequest != nil)
                        }.font(.subheadline)
                    }.padding(.vertical, 6)
                }
            }
        }.refreshable { await model.refresh(); await store.refreshActive() }
    }
}

@MainActor private struct MusePresenceView: View {
    let device: Device
    let address: String
    @Environment(\.dismiss) private var dismiss
    @State private var copied = false
    private var prompt: String { MuseEnrollment.presencePrompt(name: device.name, deviceID: device.id, address: address, permissions: device.permissions) }
    var body: some View {
        NavigationStack {
            ScrollView { VStack(alignment: .leading, spacing: 16) {
                Text("使用该成员已有连接密钥，不会重新配对或更换密钥。发送到对应 Muse 账号，按实际后台能力验证。").font(.callout).foregroundStyle(.secondary)
                Button { DesktopPasteboard.copy(prompt); copied = true } label: { Label(copied ? "保持在线提示词已成功复制" : "复制保持在线提示词", systemImage: copied ? "checkmark.circle.fill" : "doc.on.doc").frame(maxWidth: .infinity, minHeight: 44) }.buttonStyle(.borderedProminent)
                Text(prompt).font(.callout).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
            }.padding() }.navigationTitle("保持在线")
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
        }
    }
}

private struct DeviceConfirmation: Identifiable {
    enum Kind { case enable, rotate, revoke, archive, delete }
    let device: Device
    let kind: Kind
    var id: String { device.id }
    var title: String { kind == .enable ? "启用并重新配对" : kind == .delete ? "删除 Agent" : kind == .rotate ? "重发邀请" : kind == .revoke ? "撤销接入" : "归档设备" }
    var message: String { kind == .enable ? "将生成新邀请，旧邀请和凭证立即失效。完成重新配对后启用，权限保持不变。" : kind == .delete ? "将立即撤销接入权限，从成员列表移除，未完成任务标记失败。历史消息和任务保留，名称可重新使用。" : kind == .rotate ? "旧配对码和机器凭证会立即失效，设备需要重新连接。" : kind == .revoke ? "将停止这台设备访问聊天服务，历史消息保留。" : "将撤销并归档这台设备，未完成任务标记失败，历史记录保留。名称可重新使用；再次接入需创建新成员。" }
}

@MainActor private struct MemberPermissionsView: View {
    @ObservedObject var model: DevicesModel
    let device: Device
    @Environment(\.dismiss) private var dismiss
    @State private var permissions: DevicePermissions
    init(model: DevicesModel, device: Device) { self.model = model; self.device = device; _permissions = State(initialValue: device.permissions) }
    var body: some View {
        Form {
            Section(device.name) {
                Toggle("允许读取消息", isOn: $permissions.read).onChange(of: permissions.read) { _, value in if !value { permissions.execute = false; permissions.delegate = false } }
                Toggle("允许执行任务", isOn: $permissions.execute).disabled(!permissions.read).onChange(of: permissions.execute) { _, value in if !value { permissions.delegate = false } }
                Toggle("允许委派任务", isOn: $permissions.delegate).disabled(!permissions.execute)
            }.disabled(model.busy || model.pendingSettings != nil || model.pendingCloudRequest != nil)
            Section { Text("权限保存后由服务端立即执行。启用不代表授予执行或委派权限；运行中的任务结束后才能收回权限。").font(.caption).foregroundStyle(.secondary) }
            if let error = model.error { Section { Text(error).foregroundStyle(.red) } }
            Section {
                Button(model.pendingSettings == nil ? "保存权限" : "用原设置重试") {
                    Task {
                        do {
                            let request = try model.pendingSettings ?? DeviceSettingsRequest(device: device, permissions: permissions)
                            if await model.configure(request) { dismiss() }
                        } catch { model.error = error.localizedDescription }
                    }
                }.disabled(model.busy)
            }
        }.navigationTitle("修改权限")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button(model.selectedMemberID == nil ? "返回列表" : "返回成员设置") { dismiss() }.disabled(model.busy) } }
    }
}

@MainActor private struct AddMemberView: View {
    @ObservedObject var model: DevicesModel
    let completed: () -> Void
    @State private var name = ""
    @State private var type: DeviceKind = .mac
    @State private var machine = ""
    @State private var execute = false
    @State private var delegate = false
    var body: some View {
        Form {
            Section("新成员") {
                TextField("名称，例如工作室 Mac", text: $name)
                Picker("设备类型", selection: $type) { ForEach(DeviceKind.allCases) { kind in Text(kind.label).tag(kind) } }
            }.disabled(model.pendingRequest != nil || model.busy)
            Section {
                DisclosureGroup("可选设置") {
                    TextField("位置或备注（可不填）", text: $machine)
                    Toggle("允许执行任务", isOn: $execute).onChange(of: execute) { _, value in if !value { delegate = false } }
                    Toggle("允许委派任务", isOn: $delegate).disabled(!execute)
                }
                Text("默认只读，执行和委派默认关闭。仅在明确授权后开启执行，并在设备上手动启动执行器。").font(.caption).foregroundStyle(.secondary)
            }.disabled(model.pendingRequest != nil || model.busy)
            if let error = model.error { Section { Text(error).foregroundStyle(.red).font(.callout) } }
            Section {
                Button {
                    Task {
                        do {
                            let request = try model.pendingRequest ?? DeviceInviteRequest(name: name, type: type, machine: machine, permissions: .init(execute: execute, delegate: delegate))
                            if await model.invite(request) { completed() }
                        } catch { model.error = error.localizedDescription }
                    }
                } label: { HStack { Text(model.pendingRequest == nil ? "生成连接邀请" : "用原请求重试"); Spacer(); if model.busy { ProgressView() } else { Image(systemName: "arrow.right") } } }
                    .disabled(model.busy || (model.pendingRequest == nil && name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty))
                Text(type == .muse ? "Muse 在云端运行。创建后把接入提示词发送到对应账号，接入包可作为附件发送。" : "创建后会显示一次性配对码，并提供一个接入包。请在成员实际运行的设备上完成连接。").font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

@MainActor private struct ConnectMemberView: View {
    @Environment(\.roomAccent) private var accent
    @ObservedObject var model: DevicesModel
    @EnvironmentObject var store: RoomStore
    let setup: DeviceSetup
    @Environment(\.scenePhase) private var phase
    @State private var reveal = false
    @State private var copied: String?
    @State private var document: ConnectionPackage?
    @State private var exporting = false
    @State private var shareURL: URL?
    @State private var confirmCloudKey = false
    private var musePrompt: String { MuseEnrollment.prompt(name: setup.name, deviceID: setup.id, invitationID: setup.invitation?.invitationID, address: store.address, permissions: setup.permissions) }
    var body: some View {
        Form {
            Section { Label(setup.name, systemImage: "checkmark.circle.fill").font(.headline).foregroundStyle(accent); Text(setup.type.label + " · " + setup.permissions.label).font(.caption).foregroundStyle(.secondary) }
            if setup.type == .muse {
                cloudKeySection
                Section("2 · 发送提示词到 Muse 云端会话") {
                    Text("先生成成员连接密钥并录入 Muse 的安全凭证入口，再发送下方无秘密提示词，验证后开始接收任务。无需在自己的电脑或手机上运行程序。").font(.callout)
                    Text(musePrompt).font(.caption).textSelection(.enabled)
                    Button { copy(musePrompt, label: "接入提示词", expires: Date().addingTimeInterval(600)) } label: { Label("复制接入提示词", systemImage: "doc.on.doc") }
                    ShareLink(item: musePrompt) { Label("发送接入提示词", systemImage: "square.and.arrow.up") }
                    Text("提示词不含秘密。请录入成员连接密钥，不要把配对码填入 Bearer token 栏；提示词不会自动赋予平台能力。").font(.caption).foregroundStyle(.secondary)
                }
            }
            if let copied { Section { Label(copied, systemImage: "checkmark.circle.fill").foregroundStyle(.green) } }
            if setup.type != .muse { TimelineView(.periodic(from: .now, by: 1)) { _ in
                Section("一次性配对码") {
                    if let invitation = setup.invitation, invitation.hasValidCode, let code = invitation.pairingCode {
                        if reveal { Text(code).font(.system(.callout, design: .monospaced)).textSelection(.enabled) }
                        else { Text("••••••••••••••••••••••••").font(.system(.callout, design: .monospaced)) }
                        Button { if invitation.hasValidCode { copy(code, label: "配对码", expires: Date(timeIntervalSince1970: invitation.expires ?? 0)) } } label: { Label("复制配对码", systemImage: "doc.on.doc") }.buttonStyle(.borderedProminent)
                        Button(reveal ? "隐藏配对码" : "显示配对码") { reveal.toggle() }.buttonStyle(.bordered)
                        if let expires = invitation.expires { Text("有效至 " + Date(timeIntervalSince1970: expires).formatted(date: .omitted, time: .standard)).font(.caption).foregroundStyle(.secondary) }
                        Text(setup.type == .muse ? "仅本次显示；通过 Muse 平台安全凭证入口单独提供，不放进提示词或接入包。切后台后清除。平台没有安全入口时，先由 Muse 说明所需设置。" : "仅本次显示；在目标设备终端隐藏输入。不要把配对码发给 AI 或放进接入包。离开页面或 App 进入后台后隐藏并清除。").font(.caption).foregroundStyle(.secondary)
                    } else {
                        Text(setup.invitation?.notice ?? (setup.invitation == nil ? "已配对设备可使用原配置连接。" : "配对码已隐藏、过期或未能取回。请返回列表重发邀请获取新配对码。"))
                        Text("重发邀请会使旧配对码和机器凭证失效。").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            }
            if !setup.isLegacy { Section(setup.type == .muse ? "发送接入包（可选）" : "1 · 保存接入包到目标设备") {
                if setup.type == .muse {
                    Button {
                        Task {
                            if let data = await model.bundle(), phase == .active {
                                do {
                                    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
                                    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
                                    let url = directory.appendingPathComponent("ai-group-connect.pyz")
                                    try data.write(to: url, options: [.atomic])
                                    removeShareFile(); shareURL = url
                                } catch { model.error = "准备附件失败：" + error.localizedDescription }
                            }
                        }
                    } label: { Label("准备接入包附件", systemImage: "paperclip") }.disabled(model.busy)
                    if let shareURL { ShareLink(item: shareURL) { Label("发送接入包", systemImage: "square.and.arrow.up") } }
                }
                Button { Task { if let data = await model.bundle(), phase == .active { document = .init(data: data); exporting = true } } } label: { Label("下载并保存 / 分享接入包", systemImage: "square.and.arrow.up") }.disabled(model.busy)
                Text(setup.type == .muse ? "把包作为附件发送到同一个 Muse 云端会话，供其检查配置。若 Muse 不支持 .pyz，可将扩展名改为 .zip 后发送；文件是 ZIP 格式。上传文件不代表已接入。" : "接入包不含配对码或机器凭证。可通过系统文件保存到 iCloud Drive，再从目标设备打开。").font(.caption).foregroundStyle(.secondary)
            }
            } else { Section("恢复已有成员") { Text("沿用原连接器与任务检查入口，只更新安全凭证库中的密钥。成员身份、权限和历史记录保持不变。") } }
            if setup.type != .muse { Section("2 · 在目标设备运行") {
                Text("python3 ai-group-connect.pyz").font(.system(.body, design: .monospaced)).textSelection(.enabled)
                Text("需要 Python 3.10 或更新版本。按提示输入配对码，即可生成配置并验证连接。").font(.callout)
                if setup.type == .windows { Text("Windows 使用 WSL2 终端运行。").font(.callout) }
                if setup.permissions.execute && setup.type != .muse { Text("先在目标设备安装并登录 Codex CLI。接入程序只配置连接，不自动执行。审查隔离目录和权限后，手动运行接入包的 --run；后台运行需另行启用。").font(.callout) }
                else { Text("把生成的 connector.example.json 导入支持 stdio MCP 的应用。").font(.callout) }
            } }
            if let error = model.error { Section { Text(error).foregroundStyle(.red).font(.callout) } }
            Section { Button(model.selectedMemberID == nil ? "完成，查看连接状态" : "完成，返回成员设置") { model.dismissSetup(); Task { await model.refresh() } }.disabled(model.busy) }
        }.buttonStyle(.borderless).confirmationDialog("生成新成员连接密钥？旧密钥和配对邀请会立即失效，只显示本次。", isPresented: $confirmCloudKey, titleVisibility: .visible) {
            Button("生成新密钥") { reveal = false; copied = nil; Task { await model.issueCloudCredential() } }
            Button("取消", role: .cancel) {}
        }.fileExporter(isPresented: $exporting, document: document, contentType: .data, defaultFilename: "ai-group-connect.pyz") { result in
            switch result { case .success: copied = "接入包已成功导出"; case .failure(let error): model.error = "保存接入包失败：" + error.localizedDescription }
            document = nil
        }.onChange(of: phase) { _, value in if value != .active { reveal = false; copied = nil; model.concealCode() } }
            .onDisappear { copied = nil; model.concealCode(); removeShareFile() }
    }
    private var cloudKeySection: some View {
        Section("1 · 成员连接密钥（Bearer token）") {
            TimelineView(.periodic(from: .now, by: 1)) { _ in
                if let credential = model.cloudCredential, credential.memberID == setup.id, credential.canCopy, let key = credential.token {
                    VStack(alignment: .leading, spacing: 14) {
                        Text(reveal ? key : "••••••••••••••••••••••••").font(.system(.callout, design: .monospaced))
                            .accessibilityIdentifier("member-credential-value")
                        HStack(spacing: 12) {
                            Button {
                                if credential.canCopy { copy(key, label: "成员连接密钥", expires: Date(timeIntervalSince1970: credential.displayUntil ?? 0)) }
                            } label: { Label("复制密钥", systemImage: "doc.on.doc").frame(minHeight: 28) }
                                .buttonStyle(.borderedProminent)
                                .accessibilityLabel("复制成员连接密钥")
                                .accessibilityIdentifier("copy-member-credential")
                            Button { reveal.toggle() } label: {
                                Label(reveal ? "隐藏密钥" : "显示密钥", systemImage: reveal ? "eye.slash" : "eye").frame(minHeight: 28)
                            }.buttonStyle(.bordered)
                                .accessibilityIdentifier("toggle-member-credential-visibility")
                        }
                        Text("只显示本次，5 分钟后隐藏。密钥持续有效直到重新生成或撤销；隐藏不代表撤销。").font(.caption).foregroundStyle(.secondary)
                    }
                } else { Text(model.cloudCredential?.notice ?? "配对码不能当成员连接密钥使用。生成新密钥后，填入 Muse 的安全凭证入口。").font(.callout) }
            }
            Button(model.pendingCloudRequest == nil ? "生成成员连接密钥" : "用原请求确认结果") {
                if model.pendingCloudRequest != nil { Task { await model.issueCloudCredential() } }
                else { confirmCloudKey = true }
            }.buttonStyle(.bordered).disabled(model.busy)
            Text("密钥不会进入提示词或接入包。不要发进普通聊天。401 时重新生成、重新录入本成员密钥，等待刷新不能修复错误凭证。").font(.caption).foregroundStyle(.secondary)
        }
    }
    private func copy(_ value: String, label: String, expires: Date) {
        guard phase == .active, expires > Date() else { copied = nil; return }
        DesktopPasteboard.copy(value, expires: expires)
        copied = label + "已成功复制"
        NSAccessibility.post(element: NSApp as Any, notification: .announcementRequested, userInfo: [.announcement: copied ?? "已复制", .priority: NSAccessibilityPriorityLevel.medium.rawValue])
    }
    private func removeShareFile() { if let shareURL { try? FileManager.default.removeItem(at: shareURL.deletingLastPathComponent()) }; shareURL = nil }
}
