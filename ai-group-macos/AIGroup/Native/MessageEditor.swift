import SwiftUI
import AppKit
struct MessageEditor: NSViewRepresentable {
    @Binding var text: String
    @Binding var caret: Int
    let onSend: () -> Void
    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView(); scroll.hasVerticalScroller = true; scroll.drawsBackground = false
        let view = DesktopMessageTextView()
        view.isRichText = false; view.isAutomaticQuoteSubstitutionEnabled = false
        view.isAutomaticDashSubstitutionEnabled = false; view.isAutomaticTextReplacementEnabled = false
        view.drawsBackground = false; view.font = .systemFont(ofSize: 14); view.textColor = .labelColor
        view.textContainerInset = NSSize(width: 10, height: 10)
        view.isVerticallyResizable = true; view.isHorizontallyResizable = false
        view.autoresizingMask = [.width]; view.textContainer?.widthTracksTextView = true
        view.delegate = context.coordinator; view.onSend = onSend
        view.setAccessibilityLabel("任务输入框")
        scroll.documentView = view; return scroll
    }
    func updateNSView(_ scroll: NSScrollView, context: Context) {
        context.coordinator.parent = self
        guard let view = scroll.documentView as? DesktopMessageTextView else { return }
        view.onSend = onSend
        guard !view.hasMarkedText() else { return }
        if view.string != text { view.string = text }
        let location = min(max(0, caret), (view.string as NSString).length)
        if view.selectedRange().location != location { view.setSelectedRange(NSRange(location: location, length: 0)) }
    }
    final class Coordinator: NSObject, NSTextViewDelegate {
        var parent: MessageEditor
        init(_ parent: MessageEditor) { self.parent = parent }
        func textDidChange(_ notification: Notification) {
            guard let view = notification.object as? NSTextView else { return }
            parent.text = view.string; parent.caret = view.selectedRange().location
        }
        func textViewDidChangeSelection(_ notification: Notification) {
            guard let view = notification.object as? NSTextView else { return }
            // Avoid updating a SwiftUI binding synchronously inside updateNSView.
            let location = view.selectedRange().location
            DispatchQueue.main.async { if self.parent.caret != location { self.parent.caret = location } }
        }
    }
}
@MainActor final class DesktopMessageTextView: NSTextView {
    var onSend: () -> Void = {}
    static func shouldSend(keyCode: UInt16, modifiers: NSEvent.ModifierFlags, marked: Bool) -> Bool {
        (keyCode == 36 || keyCode == 76) && !marked && modifiers.intersection([.shift, .option, .control]).isEmpty
    }
    override func keyDown(with event: NSEvent) {
        if Self.shouldSend(keyCode: event.keyCode, modifiers: event.modifierFlags, marked: hasMarkedText()) { onSend(); return }
        super.keyDown(with: event)
    }
}
