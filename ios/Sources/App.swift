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
    @Published var detail: String?        // e.g. "120 / 800 MB · 5.2 MB/s"
    var started = Date()
    @Published var tab = 0
    @Published var showXMB = false

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
                if (try? FileManager.default.moveItem(at: url, to: local)) == nil { try FileManager.default.copyItem(at: url, to: local) }
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
        busy = title; progress = nil; detail = nil; started = Date()
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
                .onAppear {
                    app.reopenLast()
                    // launch arguments used for screenshots: -tab N, -xmb
                    let a = ProcessInfo.processInfo.arguments
                    if let i = a.firstIndex(of: "-tab"), i + 1 < a.count, let t = Int(a[i + 1]) { app.tab = t }
                    if a.contains("-xmb") { DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { app.showXMB = true } }
                }
        }
    }
}

struct RootView: View {
    @EnvironmentObject var app: AppState

    /// time left from the progress so far
    func remaining(_ p: Double) -> String? {
        let t = Date().timeIntervalSince(app.started)
        guard p > 0.01, t > 1.5, p < 1 else { return nil }
        let left = Int(t * (1 - p) / p)
        return left >= 3600 ? String(format: "%d:%02d:%02d", left / 3600, left / 60 % 60, left % 60) : String(format: "%d:%02d", left / 60, left % 60)
    }
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
                    if let p = app.progress {
                        ProgressView(value: p)
                        HStack {
                            Text("\(Int(p * 100))%").font(.caption.monospacedDigit().bold())
                            Spacer()
                            if let r = remaining(p) { Text("متبقي \(r)").font(.caption.monospacedDigit()).foregroundStyle(.secondary) }
                        }
                        if let d = app.detail { Text(d).font(.caption2.monospacedDigit()).foregroundStyle(.secondary).environment(\.layoutDirection, .leftToRight) }
                    } else { ProgressView() }
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
