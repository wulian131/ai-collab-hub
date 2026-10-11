import SwiftUI
import UIKit

final class SendTextView: UITextView {
    override var keyCommands: [UIKeyCommand]? { (super.keyCommands ?? []) + [UIKeyCommand(input: "\r", modifierFlags: .shift, action: #selector(newLine))] }
    @objc private func newLine() { if let range = selectedTextRange { replace(range, withText: "\n"); delegate?.textViewDidChange?(self) } }
}
@MainActor struct MessageEditor: UIViewRepresentable {
    @Binding var text: String
    @Binding var caret: Int
    var onSend: () -> Void
    func makeUIView(context: Context) -> SendTextView {
        let view = SendTextView()
        view.font = .preferredFont(forTextStyle: .body); view.adjustsFontForContentSizeCategory = true
        view.backgroundColor = .clear; view.isScrollEnabled = true; view.returnKeyType = .send
        view.textContainerInset = UIEdgeInsets(top: 12, left: 5, bottom: 12, right: 5)
        view.delegate = context.coordinator; view.accessibilityLabel = "消息内容"
        view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        return view
    }
    func updateUIView(_ view: SendTextView, context: Context) {
        context.coordinator.parent = self
        guard view.markedTextRange == nil else { return }
        if view.text != text { view.text = text; let pos = min(caret, (text as NSString).length); view.selectedRange = NSRange(location: pos, length: 0) }
    }
    func makeCoordinator() -> Coordinator { Coordinator(self) }
    final class Coordinator: NSObject, UITextViewDelegate {
        var parent: MessageEditor
        init(_ parent: MessageEditor) { self.parent = parent }
        func textViewDidChange(_ view: UITextView) { parent.text = view.text; parent.caret = view.selectedRange.location }
        func textViewDidChangeSelection(_ view: UITextView) { parent.caret = view.selectedRange.location }
        func textView(_ view: UITextView, shouldChangeTextIn range: NSRange, replacementText text: String) -> Bool {
            if text == "\n", view.markedTextRange == nil { parent.onSend(); return false }
            return true
        }
    }
}
