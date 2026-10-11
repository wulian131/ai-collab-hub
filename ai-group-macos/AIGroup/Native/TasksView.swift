import SwiftUI
@MainActor struct TasksView: View {
    @Environment(\.roomAccent) private var accent
    @EnvironmentObject var store: RoomStore
    @State private var filter: TaskFilter = .active
    @State private var recovering: Job?
    @State private var reason = ""
    private var visibleJobs: [Job] { store.jobs.filter(filter.includes) }
    var body: some View {
        List {
            Section {
                Text(store.selectedConversation?.title ?? "请先选择一个对话").font(.headline)
                if let date = store.lastSync { Text("状态截至 " + date.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary) }
                Picker("任务范围", selection: $filter) { ForEach(TaskFilter.allCases, id: \.self) { choice in Text(choice.title + " \(store.jobs.filter(choice.includes).count)").tag(choice) } }.pickerStyle(.segmented)
            }
            if visibleJobs.isEmpty {
                Section {
                    EmptyState(title: filter == .active ? "暂无进行中的任务" : filter == .attention ? "暂无需要处理的任务" : "还没有任务", detail: filter == .all ? "在聊天中选择“分派任务”后，进度会显示在这里。" : "可切换“全部”查看已返回的任务与历史。", icon: "checkmark.circle")
                    if filter != .all { Button("查看全部任务") { filter = .all }.frame(maxWidth: .infinity) }
                }
            }
            ForEach(visibleJobs) { job in
                Section {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack(spacing: 12) {
                            Avatar(id: job.target, name: store.memberName(job.target))
                            VStack(alignment: .leading, spacing: 4) {
                                Text(store.memberName(job.target)).font(.headline)
                                Text("任务 #\(job.messageID) · 执行 #\(job.id)").font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                        }
                        if let original = store.messages.first(where: { $0.id == job.messageID }) { Text(original.text).font(.subheadline).foregroundStyle(.secondary).lineLimit(2) }
                        StatusPill(text: job.stateLabel, color: job.status == "failed" || job.blocked == 1 ? .orange : accent)
                        if !job.events.isEmpty {
                            DisclosureGroup("查看时间线 · 尝试 \(job.attempts) 次") {
                                ForEach(Array(job.events.enumerated()), id: \.offset) { _, event in
                                    HStack(alignment: .top) {
                                        Text(eventLabel(event.state)); Spacer()
                                        Text(timestamp(event.created)).multilineTextAlignment(.trailing)
                                    }.font(.caption).foregroundStyle(.secondary).padding(.vertical, 4)
                                }
                            }.font(.subheadline)
                        }
                        if store.identity?.me == "owner", store.canWrite, job.status == "failed" || (job.status == "done" && job.blocked == 1) { Button("恢复此任务") { recovering = job; reason = "" }.font(.subheadline).frame(minHeight: 44) }
                    }.padding(.vertical, 6)
                }
            }
            if store.jobHasMore { Button("加载更早任务") { Task { await store.earlierJobs() } } }
        }.listStyle(.inset).navigationTitle("任务").refreshable { await store.refreshActive() }
        .alert("恢复任务", isPresented: Binding(get: { recovering != nil }, set: { if !$0 { recovering = nil } })) {
            TextField("恢复原因", text: $reason)
            Button("恢复") { if let job = recovering { Task { await store.retryJob(job, reason: reason) } }; recovering = nil }.disabled(reason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            Button("取消", role: .cancel) {}
        } message: { Text("这会重新入队执行。请填写恢复原因；运行中和已完成的任务不能重复恢复。") }
    }
    private func eventLabel(_ state: String) -> String { ["queued":"收到", "claimed":"已领取", "processing":"处理中", "done":"返回", "failed":"失败", "waiting":"等待协作", "blocked":"等待补充", "pending":"等待领取"][state] ?? state }
}
