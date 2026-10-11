import SwiftUI
@main struct AIGroupApp: App {
    @AppStorage("appearance") private var appearance = "system"
    @StateObject private var store = RoomStore()
    var body: some Scene {
        WindowGroup { RootView().environmentObject(store).frame(minWidth: 860, minHeight: 600) }
            .defaultSize(width: 1180, height: 780)
            .commands {
                CommandGroup(replacing: .newItem) { Button("新建对话") { NotificationCenter.default.post(name: .desktopNewConversation, object: nil) }.keyboardShortcut("n").disabled(!store.canWrite) }
                CommandMenu("协作") {
                    Button("搜索对话") { NotificationCenter.default.post(name: .desktopSearch, object: nil) }.keyboardShortcut("f")
                    Button("刷新对话") { Task { await store.refreshConversations(); await store.refreshActive() } }.keyboardShortcut("r")
                }
            }
        Settings { SettingsView().environmentObject(store).preferredColorScheme(appearance == "dark" ? .dark : appearance == "light" ? .light : nil).frame(width: 520, height: 500) }
    }
}
