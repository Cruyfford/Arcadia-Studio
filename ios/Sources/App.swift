import SwiftUI
import UniformTypeIdentifiers

extension UTType {
    static let arcadia = UTType(exportedAs: "com.cruyfford.arcadia")
}

final class AppState: ObservableObject {
    @Published var store: StoreProject?
    @Published var message: String?
    @Published var busy: String?
    @Published var progress: Double?
    @Published var tab = 0

    static var projectsDir: URL {
        let d = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("Projects")
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }

    /// projects are copied into the app (visible in the Files app under "Arcadia Studio")
    func open(_ url: URL) {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            var local = url
            if !url.path.hasPrefix(AppState.projectsDir.path) {
                local = AppState.projectsDir.appendingPathComponent(url.lastPathComponent)
                try? FileManager.default.removeItem(at: local)
                try FileManager.default.copyItem(at: url, to: local)
            }
            store = try StoreProject.load(local)
            UserDefaults.standard.set(local.lastPathComponent, forKey: "last_project")
            message = "تم فتح المشروع: " + local.lastPathComponent
        } catch {
            message = "تعذّر فتح المشروع: \(error.localizedDescription)"
        }
    }

    func reopenLast() {
        guard store == nil, let name = UserDefaults.standard.string(forKey: "last_project") else { return }
        let u = AppState.projectsDir.appendingPathComponent(name)
        if FileManager.default.fileExists(atPath: u.path) { store = try? StoreProject.load(u) }
    }

    func save() {
        guard let s = store else { return }
        do { try s.save(); message = "✔ تم حفظ المشروع" } catch { message = "تعذّر الحفظ: \(error.localizedDescription)" }
    }

    func run(_ title: String, _ job: @escaping (_ progress: @escaping (Double) -> Void) async throws -> String?) {
        busy = title; progress = nil
        Task {
            do {
                let msg = try await job { p in DispatchQueue.main.async { self.progress = p } }
                await MainActor.run { self.busy = nil; if let m = msg { self.message = m } }
            } catch {
                await MainActor.run { self.busy = nil; self.message = "✖ " + error.localizedDescription }
            }
        }
    }
}

@main
struct ArcadiaStudioApp: App {
    @StateObject var app = AppState()
    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(app)
                .environment(\.layoutDirection, .rightToLeft)
                .preferredColorScheme(.dark)
                .tint(Color(red: 0.49, green: 0.23, blue: 0.93))
                .onOpenURL { app.open($0) }
                .onAppear { app.reopenLast() }
        }
    }
}

struct RootView: View {
    @EnvironmentObject var app: AppState
    var body: some View {
        ZStack(alignment: .bottom) {
            TabView(selection: $app.tab) {
                StoreHome().tabItem { Label("المتجر", systemImage: "storefront") }.tag(0)
                DeviceView().tabItem { Label("الجهاز", systemImage: "gamecontroller") }.tag(1)
                LinkCheckView().tabItem { Label("الروابط", systemImage: "link") }.tag(2)
                DownloadsView().tabItem { Label("التنزيلات", systemImage: "arrow.down.circle") }.tag(3)
                PkgViewerView().tabItem { Label("PKG", systemImage: "shippingbox") }.tag(4)
            }
            if let b = app.busy {
                VStack(spacing: 8) {
                    Text(b).font(.subheadline.bold())
                    if let p = app.progress { ProgressView(value: p) } else { ProgressView() }
                }
                .padding(14).frame(maxWidth: 420)
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
                .padding(.bottom, 70).padding(.horizontal)
            }
        }
        .alert(app.message ?? "", isPresented: Binding(get: { app.message != nil }, set: { if !$0 { app.message = nil } })) {
            Button("حسناً", role: .cancel) {}
        }
    }
}
