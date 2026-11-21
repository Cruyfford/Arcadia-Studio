import SwiftUI
import UniformTypeIdentifiers

struct DeviceView: View {
    @EnvironmentObject var app: AppState
    @AppStorage("ps3_ip") var ip = ""
    @State private var status = "—"
    @State private var pickPkg = false
    @FocusState private var ipFocused: Bool

    var body: some View {
        NavigationStack {
            Form {
                Section("جهاز PS3 (webMAN MOD)") {
                    TextField("IP الجهاز مثل 192.168.1.20", text: Binding(get: { ip }, set: { ip = PS3.cleanIP($0) }))
                        .keyboardType(.decimalPad).autocorrectionDisabled().textInputAutocapitalization(.never)
                        .multilineTextAlignment(.leading).environment(\.layoutDirection, .leftToRight)
                        .focused($ipFocused).submitLabel(.done)
                    HStack { Text("الحالة"); Spacer(); Text(status).foregroundStyle(.secondary) }
                    Button { test() } label: { Label("اختبار الاتصال", systemImage: "antenna.radiowaves.left.and.right") }.disabled(ip.isEmpty)
                }
                Section("الإرسال") {
                    Button { pickPkg = true } label: { Label("إرسال حزمة PKG وتثبيتها", systemImage: "square.and.arrow.up.on.square") }.disabled(ip.isEmpty)
                    NavigationLink { FTPBrowser(path: "/dev_hdd0") } label: { Label("ملفات الجهاز (FTP)", systemImage: "folder") }.disabled(ip.isEmpty)
                }
                Section {
                    Text("الجهاز والجوال لازم يكونون على نفس شبكة الواي فاي، و webMAN MOD شغّال على الجهاز.").font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("الجهاز")
            .scrollDismissesKeyboard(.interactively)
            .toolbar {
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("تم") { ipFocused = false }.bold()
                }
            }
            .docPicker(isPresented: $pickPkg) { send($0[0]) }
        }
    }

    func test() {
        ipFocused = false
        status = "⏳"
        Task {
            let (f, w) = await PS3(ip: ip).test()
            await MainActor.run {
                status = "FTP \(f ? "✅" : "❌")   webMAN \(w ? "✅" : "❌")"
                if !f && !w { app.message = ("الجهاز ما رد: تأكد من الـ IP، وإن webMAN MOD مثبّت وشغّال (HFW Tools ← Update ← Homebrew Apps).") }
            }
        }
    }

    func send(_ u: URL) {
        let ip = self.ip
        let info = PS3.pkgInfo(u)
        app.run("جاري إرسال \(u.lastPathComponent)...") { prog in
            let ps3 = PS3(ip: ip)
            // 1) upload
            let t0 = Date()
            let remote = try await ps3.sendPKG(u, install: false) { a, b in
                prog(Double(a) / Double(max(b, 1)))
                let sp = Double(a) / max(Date().timeIntervalSince(t0), 0.1)
                let d = "\(human(a)) / \(human(b))  ·  \(human(Int64(sp)))/s"
                DispatchQueue.main.async { app.detail = d }
            }
            // 2) size before the install (a reinstall over an existing game)
            var baseline: Int64 = 0
            if let info = info, let f = try? await ps3.ftp() {
                baseline = await ps3.installedBytes(f, title: info.title).0; f.close()
            }
            try await ps3.install(remote)
            guard let info = info, info.size > 0 else {
                return "✔ تم إرسال الحزمة للجهاز.\nالتثبيت بدأ على الجهاز، وتقدّمه يطلع على شاشة التلفزيون."
            }
            // 3) follow the install on the console (approximate)
            await MainActor.run {
                app.busy = "جاري التثبيت على الجهاز (\(info.title))..."; app.progress = 0; app.detail = nil; app.started = Date()
            }
            let ok = await ps3.watchInstall(title: info.title, expected: info.size, baseline: baseline) { p, grown in
                prog(p)
                let d = "\(human(grown)) / \(human(info.size))  ·  تقريبي"
                DispatchQueue.main.async { app.detail = d }
            }
            return ok ? "✔ تم التثبيت على الجهاز (\(info.title))."
                      : "✔ تم إرسال الحزمة وبدأ التثبيت.\nما قدرت أتابع التقدّم من الجوال، شوف شاشة التلفزيون."
        }
    }

}

struct FTPBrowser: View {
    @EnvironmentObject var app: AppState
    @AppStorage("ps3_ip") var ip = ""
    let path: String
    @State private var items: [FTPEntry] = []
    @State private var loading = true
    @State private var error: String?
    @State private var uploading = false
    @State private var renaming: FTPEntry?
    @State private var newName = ""
    @State private var newFolder = false
    @State private var shareURL: URL?

    var body: some View {
        List {
            if let e = error { Text(e).foregroundStyle(.red) }
            ForEach(items) { e in
                Group {
                    if e.isDir {
                        NavigationLink { FTPBrowser(path: join(e.name)) } label: { row(e) }
                    } else {
                        row(e)
                    }
                }
                .contextMenu {
                    if !e.isDir {
                        Button { download(e) } label: { Label("تنزيل إلى الجوال", systemImage: "arrow.down.circle") }
                        if e.name.lowercased().hasSuffix(".pkg") {
                            Button { install(e) } label: { Label("تثبيت الحزمة على الجهاز", systemImage: "shippingbox") }
                        }
                    }
                    Button { renaming = e; newName = e.name } label: { Label("إعادة تسمية", systemImage: "pencil") }
                    Button { UIPasteboard.general.string = join(e.name) } label: { Label("نسخ المسار", systemImage: "doc.on.doc") }
                    Button(role: .destructive) { delete(e) } label: { Label("حذف", systemImage: "trash") }
                }
            }
        }
        .overlay { if loading { ProgressView() } }
        .refreshable { await load() }
        .navigationTitle((path as NSString).lastPathComponent.isEmpty ? "/" : (path as NSString).lastPathComponent)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            Menu {
                Button { uploading = true } label: { Label("رفع ملف إلى هنا", systemImage: "square.and.arrow.up") }
                Button { newFolder = true; newName = "" } label: { Label("مجلد جديد", systemImage: "folder.badge.plus") }
                Button { Task { await load() } } label: { Label("تحديث", systemImage: "arrow.clockwise") }
            } label: { Image(systemName: "ellipsis.circle") }
        }
        .docPicker(isPresented: $uploading, multiple: true) { upload($0) }
        .alert("إعادة تسمية", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("الاسم", text: $newName)
            Button("حفظ") { if let e = renaming { rename(e, newName) } }
            Button("إلغاء", role: .cancel) {}
        }
        .alert("مجلد جديد", isPresented: $newFolder) {
            TextField("اسم المجلد", text: $newName)
            Button("إنشاء") { mkdir(newName) }
            Button("إلغاء", role: .cancel) {}
        }
        .sheet(item: Binding(get: { shareURL.map { ShareItem(url: $0) } }, set: { shareURL = $0?.url })) { ShareSheet(url: $0.url) }
        .task { await load() }
    }

    func join(_ n: String) -> String { path == "/" ? "/" + n : path + "/" + n }

    func row(_ e: FTPEntry) -> some View {
        HStack {
            Image(systemName: e.isDir ? "folder.fill" : (e.name.lowercased().hasSuffix(".pkg") ? "shippingbox.fill" : "doc"))
                .foregroundStyle(e.isDir ? Color.accentColor : .secondary)
            VStack(alignment: .leading) {
                Text(e.name).environment(\.layoutDirection, .leftToRight)
                if !e.isDir { Text("\(human(e.size))  \(e.date)").font(.caption).foregroundStyle(.secondary) }
            }
        }
    }

    func client() async throws -> FTPClient { try await PS3(ip: ip).ftp() }

    func load() async {
        await MainActor.run { loading = true; error = nil }
        do {
            let f = try await client(); defer { f.close() }
            let r = try await f.list(path)
            await MainActor.run { items = r; loading = false }
        } catch {
            await MainActor.run { self.error = error.localizedDescription; loading = false }
        }
    }

    func op(_ title: String, _ job: @escaping (FTPClient, @escaping (Double) -> Void) async throws -> String?) {
        app.run(title) { prog in
            let f = try await client(); defer { f.close() }
            let m = try await job(f, prog)
            await load(); return m
        }
    }

    func download(_ e: FTPEntry) {
        let dst = DownloadTask.folder.appendingPathComponent(e.name)
        op("جاري تنزيل \(e.name)...") { f, prog in
            try await f.download(join(e.name), to: dst) { a, b in prog(Double(a) / Double(max(b, 1))) }
            await MainActor.run { shareURL = dst }
            return nil
        }
    }

    func install(_ e: FTPEntry) {
        let p = join(e.name), ip = self.ip
        app.run("جاري طلب التثبيت...") { _ in try await PS3(ip: ip).install(p); return "📦 طُلب تثبيت \(e.name) عبر webMAN" }
    }

    func upload(_ urls: [URL]) {
        op("جاري الرفع إلى الجهاز...") { f, prog in
            for (i, u) in urls.enumerated() {
                let scoped = u.startAccessingSecurityScopedResource()
                defer { if scoped { u.stopAccessingSecurityScopedResource() } }
                try await f.upload(file: u, to: join(u.lastPathComponent)) { a, b in
                    prog((Double(i) + Double(a) / Double(max(b, 1))) / Double(urls.count))
                }
            }
            return "✔ تم رفع \(urls.count) ملف"
        }
    }

    func delete(_ e: FTPEntry) { op("جاري الحذف...") { f, _ in try await f.delete(join(e.name), isDir: e.isDir); return nil } }
    func rename(_ e: FTPEntry, _ n: String) {
        guard !n.isEmpty, n != e.name else { return }
        op("جاري إعادة التسمية...") { f, _ in try await f.rename(join(e.name), join(n)); return nil }
    }
    func mkdir(_ n: String) {
        guard !n.isEmpty else { return }
        op("جاري إنشاء المجلد...") { f, _ in try await f.mkdirs(join(n)); return nil }
    }
}

struct ShareItem: Identifiable { let url: URL; var id: URL { url } }

struct ShareSheet: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> UIActivityViewController { UIActivityViewController(activityItems: [url], applicationActivities: nil) }
    func updateUIViewController(_ vc: UIActivityViewController, context: Context) {}
}
