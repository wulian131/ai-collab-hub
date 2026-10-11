import SwiftUI
import Foundation

struct DeviceSetup: Identifiable {
    let id: String
    let name: String
    let type: DeviceKind
    let permissions: DevicePermissions
    var invitation: DeviceInvitation?
    var isLegacy = false
}

@MainActor final class DevicesModel: ObservableObject {
    @Published var devices: [Device] = []
    @Published private(set) var didLoad = false
    @Published var busy = false
    @Published var error: String?
    @Published var setup: DeviceSetup?
    @Published var pendingRequest: DeviceInviteRequest?
    @Published var pendingSettings: DeviceSettingsRequest?
    @Published var notice: String?
    @Published var cloudCredential: CloudCredential?
    @Published var pendingCloudRequest: CloudCredentialRequest?
    private weak var store: RoomStore?
    private var acceptsSecrets = true
    private var cloudExpiry: Task<Void, Never>?
    let selectedMemberID: String?
    init(memberID: String? = nil) { selectedMemberID = memberID }
    var displayedDevices: [Device] { devices.filter { selectedMemberID == nil || $0.id == selectedMemberID } }

    func bind(_ store: RoomStore) { self.store = store }
    private func client() throws -> RoomAPI {
        guard let store else { throw ClientError.validation("登录状态已变化，请重新打开成员页") }
        return try store.deviceAPI()
    }
    private func current(_ api: RoomAPI) -> Bool { store?.isCurrentDeviceAPI(api) == true }
    private func handle(_ failure: Error, action: String? = nil) {
        guard !FailurePresentation.isCancellation(failure) else { return }
        if FailurePresentation.isConnectivity(failure) {
            store?.report(failure)
            error = action.map { $0 + "未确认，请网络恢复后重试。" }
        } else {
            if let apiError = failure as? ClientError, apiError.isUnauthorized { store?.report(failure) }
            error = failure.localizedDescription
        }
    }
    private func load(_ api: RoomAPI) async throws {
        let response = try await api.devices()
        guard current(api) else { return }
        devices = response.devices; didLoad = true
        await store?.refreshMembers()
    }
    func refresh() async {
        guard !busy else { return }
        busy = true; defer { busy = false }
        error = nil
        do { try await load(client()) } catch { handle(error) }
    }
    // One frozen invitation request survives an uncertain response while this screen stays open.
    func invite(_ request: DeviceInviteRequest, device: Device? = nil) async -> Bool {
        guard !busy, pendingSettings == nil, pendingCloudRequest == nil else { return false }
        busy = true; defer { busy = false }
        let payload = pendingRequest ?? request
        pendingRequest = payload; error = nil
        do {
            let api = try client()
            var response = try await api.inviteDevice(payload)
            guard current(api) else { pendingRequest = nil; return false }
            if !acceptsSecrets { response.pairingCode = nil }
            pendingRequest = nil
            cloudCredential = nil
            setup = DeviceSetup(id: response.deviceID, name: payload.name ?? device?.name ?? response.deviceID,
                                type: payload.type ?? device?.type ?? .linux,
                                permissions: payload.permissions ?? device?.permissions ?? .init(), invitation: response)
            do { try await load(api) } catch { handle(error, action: "列表刷新") }
            return true
        } catch {
            // Do not generate a fresh key after an ambiguous result. A replay may have no code.
            handle(error, action: "邀请")
            if !FailurePresentation.isCancellation(error) { self.error = (self.error ?? "邀请未确认。") + " 可用原请求重试；若已创建但配对码丢失，请在列表重发邀请。" }
            return false
        }
    }
    func action(_ device: Device, archive: Bool) async { await action(device, action: archive ? .archive : .revoke) }
    func action(_ device: Device, action: DeviceManagementAction) async {
        guard !busy, pendingRequest == nil, pendingSettings == nil, pendingCloudRequest == nil else { return }
        busy = true; defer { busy = false }; error = nil
        do {
            let api = try client()
            _ = try await api.deviceAction(.init(deviceID: device.id, action: action))
            guard current(api) else { return }
            if setup?.id == device.id { concealCode(); setup = nil }
            try await load(api)
            await store?.refreshActive()
        } catch { handle(error, action: "操作") }
    }
    func enable(_ device: Device) async {
        if !device.isLegacy && device.type == .muse { instructions(device); return }
        if !device.isLegacy { _ = await invite(.init(reinvite: device.id), device: device); return }
        do { _ = await configure(try .init(device: device)) } catch { handle(error) }
    }
    @discardableResult func configure(_ request: DeviceSettingsRequest) async -> Bool {
        guard !busy, pendingRequest == nil, pendingCloudRequest == nil else { return false }
        busy = true; defer { busy = false }; error = nil; notice = nil
        let payload = pendingSettings ?? request; pendingSettings = payload
        do {
            let api = try client()
            _ = try await api.configureDevice(payload)
            guard current(api) else { pendingSettings = nil; return false }
            pendingSettings = nil
            if setup?.id == payload.deviceID { concealCode(); setup = nil }
            notice = payload.action == "enable" ? "成员已启用" : "权限已保存"
            do { try await load(api); await store?.refreshActive() } catch { handle(error, action: "列表刷新") }
            return true
        } catch {
            if !FailurePresentation.isConnectivity(error) && !FailurePresentation.isCancellation(error) { pendingSettings = nil }
            handle(error, action: "设置保存")
            return false
        }
    }
    @discardableResult func issueCloudCredential() async -> Bool {
        guard !busy, pendingRequest == nil, pendingSettings == nil, let setup, setup.type == .muse else { return false }
        guard pendingCloudRequest == nil || pendingCloudRequest?.deviceID == setup.id else { error = "请先确认另一个成员的密钥请求"; return false }
        busy = true; defer { busy = false }; error = nil; notice = nil
        do {
            let api = try client()
            if pendingCloudRequest == nil {
                let list = try await api.devices()
                guard current(api) else { return false }
                guard let d = list.devices.first(where: { $0.id == setup.id }), d.type == .muse, let generation = d.generation else { throw ClientError.validation("请刷新并选择新的 Muse 成员") }
                pendingCloudRequest = .init(deviceID: setup.id, generation: generation)
            }
            guard let request = pendingCloudRequest else { return false }
            var result = try await api.cloudCredential(request)
            guard current(api), self.setup?.id == request.deviceID else { pendingCloudRequest = nil; return false }
            guard result.memberID == request.deviceID else { throw ClientError.invalidResponse }
            if !acceptsSecrets { result.token = nil }
            cloudExpiry?.cancel()
            cloudCredential = result; pendingCloudRequest = nil
            if result.token != nil, let deadline = result.displayUntil {
                let memberID = result.memberID
                cloudExpiry = Task { [weak self] in
                    do { try await Task.sleep(for: .seconds(max(0, min(300, deadline - Date().timeIntervalSince1970)))) } catch { return }
                    guard let self, self.cloudCredential?.memberID == memberID, self.cloudCredential?.displayUntil == deadline else { return }
                    self.cloudCredential?.token = nil
                }
            }
            self.setup = .init(id: setup.id, name: setup.name, type: .muse, permissions: result.permissions, invitation: nil, isLegacy: setup.isLegacy)
            do { try await load(api) } catch { handle(error, action: "列表刷新") }
            return true
        } catch {
            if !FailurePresentation.isConnectivity(error) && !FailurePresentation.isCancellation(error) { pendingCloudRequest = nil }
            handle(error, action: "密钥生成")
            return false
        }
    }
    func bundle() async -> Data? {
        guard !busy, let setup else { return nil }
        busy = true; defer { busy = false }; error = nil
        do {
            let api = try client()
            let data = try await api.deviceBundle(deviceID: setup.id, invitationID: setup.invitation?.invitationID)
            guard current(api) else { return nil }
            return data
        } catch { handle(error, action: "接入包下载"); return nil }
    }
    func setForeground(_ active: Bool) { acceptsSecrets = active; if !active { concealCode() } }
    func concealCode() { cloudExpiry?.cancel(); cloudExpiry = nil; setup?.invitation?.pairingCode = nil; cloudCredential?.token = nil }
    func dismissSetup() { concealCode(); setup = nil }
    func clear() { concealCode(); setup = nil; pendingRequest = nil; pendingSettings = nil; pendingCloudRequest = nil; cloudCredential = nil; notice = nil; devices = []; didLoad = false; error = nil }
    func instructions(_ device: Device) { concealCode(); cloudCredential = nil; setup = .init(id: device.id, name: device.name, type: device.type, permissions: device.permissions, invitation: nil, isLegacy: device.isLegacy) }
}
