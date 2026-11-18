import SwiftUI
import UniformTypeIdentifiers

/// Reliable file picker: any file type, the picked file is copied into the app first (no permission issues).
struct DocPicker: UIViewControllerRepresentable {
    var multiple = false
    let onPick: ([URL]) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onPick) }

    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let vc = UIDocumentPickerViewController(forOpeningContentTypes: [.item, .data, .content], asCopy: true)
        vc.allowsMultipleSelection = multiple
        vc.shouldShowFileExtensions = true
        vc.delegate = context.coordinator
        return vc
    }
    func updateUIViewController(_ vc: UIDocumentPickerViewController, context: Context) {}

    final class Coordinator: NSObject, UIDocumentPickerDelegate {
        let onPick: ([URL]) -> Void
        init(_ f: @escaping ([URL]) -> Void) { onPick = f }
        func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) { onPick(urls) }
    }
}

extension View {
    func docPicker(isPresented: Binding<Bool>, multiple: Bool = false, onPick: @escaping ([URL]) -> Void) -> some View {
        sheet(isPresented: isPresented) {
            DocPicker(multiple: multiple) { urls in isPresented.wrappedValue = false; if !urls.isEmpty { onPick(urls) } }
                .ignoresSafeArea()
        }
    }
}
