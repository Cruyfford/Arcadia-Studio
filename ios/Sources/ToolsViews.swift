import SwiftUI
import UniformTypeIdentifiers

// ================= link checker
struct LinkCheckView: View {
    @EnvironmentObject var app: AppState
    @State private var scope: Target?
    @State private var items: [LinkItem] = []
    @State private var running = false
    @State private var filter = 0
    @State private var checked = 0
    @State private var task: Task<Void, Never>?

    var body: some View {
        NavigationStack {
            VStack(spacing: 8) {
                if let s = app.store {
                    Picker("القسم", selection: Binding(get: { scope ?? s.root }, set: { scope = $0 })) {
                        ForEach(s.allSections().map { SecItem(name: $0.0, target: $0.1) }) { Text($0.name).tag($0.target) }
                    }.pickerStyle(.menu)
                    Picker("عرض", selection: $filter) {
                        Text("الكل").tag(0); Text("المعطلة").tag(1); Text("غير مؤكدة").tag(2); Text("السليمة").tag(3)
                    }.pickerStyle(.segmented).padding(.horizontal)
                    if !items.isEmpty {
                        let ok = items.filter { $0.status == .ok }.count, bad = items.filter { $0.status == .broken }.count
                        let uns = items.filter { $0.status == .unsure }.count
                        ProgressView(value: Double(checked), total: Double(max(items.count, 1))).padding(.horizontal)
                        Text("✅ \(ok)   ❌ \(bad)   ⚠️ \(uns)   — من \(items.count)").font(.caption)
                    }
                    List(shown) { it in
                        HStack(spacing: 10) {
                            Text(icon(it.status))
                            if let img = s.image(it.node.pairs["icon"]) { Image(uiImage: img).resizable().aspectRatio(contentMode: .fit).frame(width: 56, height: 31) }
                            VStack(alignment: .leading, spacing: 2) {
                                Text(it.title).lineLimit(2)
                                Text(it.note.isEmpty ? (it.size.map { human($0) } ?? "") : it.note).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .contextMenu {
                            Button { UIPasteboard.general.string = it.url } label: { Label("نسخ الرابط", systemImage: "doc.on.doc") }
                            Button { Downloads.shared.add(it.url, connections: 8) } label: { Label("تنزيل", systemImage: "arrow.down.circle") }
                            if let u = URL(string: it.url) { Link(destination: u) { Label("فتح في المتصفح", systemImage: "safari") } }
                            Button(role: .destructive) { s.removeNode(it.node); items.removeAll { $0.id == it.id } } label: { Label("حذف من المتجر", systemImage: "trash") }
                        }
                    }
                } else {
                    Spacer(); Text("افتح مشروع المتجر أولاً من تبويب «المتجر»").foregroundStyle(.secondary); Spacer()
                }
            }
            .navigationTitle("فحص الروابط")
            .toolbar {
                if app.store != nil {
                    Button(running ? "إيقاف" : "بدء الفحص") { running ? stop() : start() }
                }
            }
        }
    }

    var shown: [LinkItem] {
        switch filter {
        case 1: return items.filter { $0.status == .broken }
        case 2: return items.filter { $0.status == .unsure }
        case 3: return items.filter { $0.status == .ok }
        default: return items
        }
    }

    func icon(_ s: LinkStatus) -> String { ["ok": "✅", "broken": "❌", "unsure": "⚠️", "pending": "⏳"][s.rawValue] ?? "" }

    func start() {
        guard let s = app.store else { return }
        items = LinkChecker.collect(s, in: scope); checked = 0; running = true
        let urls = Array(Set(items.map { $0.url }))
        task = Task {
            await withTaskGroup(of: (String, (LinkStatus, String, Int64?)).self) { g in
                var it = urls.makeIterator()
                for _ in 0..<min(16, urls.count) { if let u = it.next() { g.addTask { (u, await LinkChecker.check(u)) } } }
                while let res = await g.next() {
                    let u = res.0, r = res.1
                    if Task.isCancelled { g.cancelAll(); break }
                    await MainActor.run {
                        for i in items.indices where items[i].url == u { items[i].status = r.0; items[i].note = r.1; items[i].size = r.2 }
                        checked += items.filter { $0.url == u }.count
                    }
                    if let nu = it.next() { g.addTask { (nu, await LinkChecker.check(nu)) } }
                }
            }
            await MainActor.run { running = false }
        }
    }
    func stop() { task?.cancel(); running = false }
}

// ================= downloads
struct DownloadsView: View {
    @ObservedObject var dl = Downloads.shared
    @AppStorage("dl_conns") var conns = 8
    @State private var adding = false
    @State private var url = ""
    @State private var share: URL?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Stepper("عدد الاتصالات: \(conns)", value: $conns, in: 1...16)
                    Text("الملفات تنحفظ في تطبيق «الملفات» ← Arcadia Studio ← Downloads").font(.caption).foregroundStyle(.secondary)
                }
                ForEach(dl.items) { d in DownloadRow(d: d, share: $share) }
            }
            .navigationTitle("التنزيلات")
            .toolbar {
                Button { url = UIPasteboard.general.string?.hasPrefix("http") == true ? UIPasteboard.general.string! : ""; adding = true } label: { Image(systemName: "plus") }
            }
            .alert("إضافة رابط", isPresented: $adding) {
                TextField("https://...", text: $url)
                Button("تنزيل") { if url.hasPrefix("http") { dl.add(url, connections: conns) } }
                Button("إلغاء", role: .cancel) {}
            }
            .sheet(item: Binding(get: { share.map { ShareItem(url: $0) } }, set: { share = $0?.url })) { ShareSheet(url: $0.url) }
        }
    }
}

struct DownloadRow: View {
    @ObservedObject var d: DownloadTask
    @Binding var share: URL?
    @EnvironmentObject var app: AppState
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(d.name).font(.subheadline.bold()).lineLimit(1).environment(\.layoutDirection, .leftToRight)
            ProgressView(value: Double(d.done), total: Double(max(d.total, 1)))
            HStack {
                Text(d.state); Spacer()
                Text("\(human(d.done)) / \(d.total > 0 ? human(d.total) : "?")")
                if d.speed > 0 { Text("• \(human(Int64(d.speed)))/ث") }
            }.font(.caption).foregroundStyle(.secondary)
        }
        .contextMenu {
            Button { d.pause() } label: { Label("إيقاف مؤقت", systemImage: "pause") }
            Button { d.resumeDownload() } label: { Label("استئناف", systemImage: "play") }
            if let f = d.file, d.state.hasPrefix("✅") {
                Button { share = f } label: { Label("مشاركة / حفظ", systemImage: "square.and.arrow.up") }
                if f.pathExtension.lowercased() == "pkg" {
                    Button { sendPS3(f) } label: { Label("إرسال للجهاز وتثبيت", systemImage: "gamecontroller") }
                }
            }
            Button(role: .destructive) { d.cancel() } label: { Label("إلغاء", systemImage: "xmark") }
        }
    }
    func sendPS3(_ f: URL) {
        let ip = UserDefaults.standard.string(forKey: "ps3_ip") ?? ""
        guard !ip.isEmpty else { app.message = "اكتب IP الجهاز أولاً"; return }
        app.run("جاري الإرسال للجهاز...") { prog in
            let r = try await PS3(ip: ip).sendPKG(f, install: true) { a, b in prog(Double(a) / Double(max(b, 1))) }
            return "تم الإرسال إلى \(r) وطُلب التثبيت"
        }
    }
}

// ================= PKG viewer
struct PkgViewerView: View {
    @EnvironmentObject var app: AppState
    @State private var reader: PkgReader?
    @State private var sfo: [String: String] = [:]
    @State private var icon: UIImage?
    @State private var pic: UIImage?
    @State private var picking = false
    @State private var share: URL?
    @State private var localURL: URL?

    var body: some View {
        NavigationStack {
            Group {
                if let r = reader {
                    List {
                        Section {
                            if let p = pic { Image(uiImage: p).resizable().aspectRatio(16 / 9, contentMode: .fit).clipShape(RoundedRectangle(cornerRadius: 10)) }
                            HStack(alignment: .top, spacing: 12) {
                                if let i = icon { Image(uiImage: i).resizable().aspectRatio(contentMode: .fit).frame(width: 130) }
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(sfo["TITLE"] ?? r.url.lastPathComponent).font(.headline)
                                    Text(r.contentID).font(.caption.monospaced()).environment(\.layoutDirection, .leftToRight)
                                    Text("\(r.kind) • \(human(Int64(r.totalSize)))").font(.caption)
                                }
                            }
                        }
                        Section("المعلومات") {
                            ForEach(sfo.keys.sorted(), id: \.self) { k in
                                HStack { Text(k).font(.caption.monospaced()); Spacer(); Text(sfo[k] ?? "").font(.caption).lineLimit(2) }
                            }
                        }
                        Section("الملفات (\(r.entries.count))") {
                            ForEach(r.entries) { e in
                                HStack {
                                    Image(systemName: e.isDir ? "folder.fill" : "doc").foregroundStyle(e.isDir ? Color.accentColor : .secondary)
                                    Text(e.name).font(.caption).environment(\.layoutDirection, .leftToRight)
                                    Spacer()
                                    if !e.isDir { Text(human(Int64(e.size))).font(.caption2).foregroundStyle(.secondary) }
                                }
                                .contextMenu { if !e.isDir { Button { extract(e) } label: { Label("استخراج ومشاركة", systemImage: "square.and.arrow.up") } } }
                            }
                        }
                    }
                } else {
                    VStack(spacing: 16) {
                        Image(systemName: "shippingbox").font(.system(size: 56)).foregroundStyle(.tint)
                        Text("اختر ملف PKG لعرض محتواه").foregroundStyle(.secondary)
                        Button { picking = true } label: { Label("فتح PKG", systemImage: "folder") }.buttonStyle(.borderedProminent)
                    }
                }
            }
            .navigationTitle("عارض PKG")
            .toolbar {
                Menu {
                    Button { picking = true } label: { Label("فتح PKG...", systemImage: "folder") }
                    if reader != nil {
                        Button { extractAll() } label: { Label("استخراج الكل", systemImage: "tray.and.arrow.down") }
                        Button { sendPS3() } label: { Label("إرسال للجهاز وتثبيت", systemImage: "gamecontroller") }
                    }
                } label: { Image(systemName: "ellipsis.circle") }
            }
            .fileImporter(isPresented: $picking, allowedContentTypes: [.data]) { r in
                if case .success(let u) = r { open(u) }
            }
            .sheet(item: Binding(get: { share.map { ShareItem(url: $0) } }, set: { share = $0?.url })) { ShareSheet(url: $0.url) }
        }
    }

    var outDir: URL {
        let base = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("Extracted")
        return base.appendingPathComponent(reader?.url.deletingPathExtension().lastPathComponent ?? "pkg")
    }

    func open(_ u: URL) {
        let scoped = u.startAccessingSecurityScopedResource()
        defer { if scoped { u.stopAccessingSecurityScopedResource() } }
        do {
            // keep a local copy so it stays readable (large files are copied once)
            let local = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("PKG")
            try? FileManager.default.createDirectory(at: local, withIntermediateDirectories: true)
            let dst = local.appendingPathComponent(u.lastPathComponent)
            if u.path != dst.path { try? FileManager.default.removeItem(at: dst); try FileManager.default.copyItem(at: u, to: dst) }
            let r = try PkgReader(dst)
            reader = r; localURL = dst
            sfo = SFO.parse(r.readFile("PARAM.SFO"))
            icon = r.readFile("ICON0.PNG").flatMap { UIImage(data: $0) }
            pic = (r.readFile("PIC1.PNG") ?? r.readFile("PIC0.PNG")).flatMap { UIImage(data: $0) }
        } catch { app.message = "تعذّر فتح الحزمة: \(error.localizedDescription)" }
    }

    func extract(_ e: PkgEntry) {
        guard let r = reader else { return }
        let dir = outDir
        app.run("جاري الاستخراج...") { _ in
            let u = try r.extract(e, to: dir)
            await MainActor.run { share = u }
            return nil
        }
    }

    func extractAll() {
        guard let r = reader else { return }
        let dir = outDir, total = r.entries.reduce(Int64(0)) { $0 + Int64($1.size) }
        app.run("جاري استخراج كل الملفات...") { prog in
            var done: Int64 = 0
            for e in r.entries { _ = try r.extract(e, to: dir) { n in done += n; prog(Double(done) / Double(max(total, 1))) } }
            return "تم الاستخراج إلى: الملفات ← Arcadia Studio ← Extracted ← \(dir.lastPathComponent)"
        }
    }

    func sendPS3() {
        guard let u = localURL else { return }
        let ip = UserDefaults.standard.string(forKey: "ps3_ip") ?? ""
        guard !ip.isEmpty else { app.message = "اكتب IP الجهاز في تبويب «الجهاز» أولاً"; return }
        app.run("جاري الإرسال للجهاز...") { prog in
            let r = try await PS3(ip: ip).sendPKG(u, install: true) { a, b in prog(Double(a) / Double(max(b, 1))) }
            return "تم الإرسال إلى \(r) وطُلب التثبيت"
        }
    }
}
