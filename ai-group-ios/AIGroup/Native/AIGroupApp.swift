import SwiftUI
@main struct AIGroupApp: App {
    @StateObject private var store = RoomStore()
    var body: some Scene { WindowGroup { RootView().environmentObject(store) } }
}
