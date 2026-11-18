import SwiftUI
import PhotosUI
import CryptoKit

// ================= PKG building (same layout as PSL1GHT's pkg.py / the Windows Studio's psl1ght_pack)
enum PkgBuilder {
    /// SHA-1 keystream used by debug packages
    static func crypt(_ key: [UInt8], _ data: [UInt8], start: UInt64) -> [UInt8] {
        var k = [UInt8](repeating: 0, count: 0x40)
        for i in 0..<8 { k[i] = key[i]; k[8 + i] = key[i]; k[16 + i] = key[8 + i]; k[24 + i] = key[8 + i] }
        var out = data
        var b = start, pos = 0
        while pos < out.count {
            for i in 0..<8 { k[0x38 + i] = UInt8((b >> (56 - 8 * UInt64(i))) & 0xFF) }
            let h = Array(Insecure.SHA1.hash(data: k))
            for i in 0..<min(16, out.count - pos) { out[pos + i] ^= h[i] }
            pos += 16; b &+= 1
        }
        return out
    }

    private static func be(_ v: UInt64, _ n: Int) -> [UInt8] { (0..<n).map { UInt8((v >> (8 * UInt64(n - 1 - $0))) & 0xFF) } }
    private static func sha1(_ d: [UInt8]) -> [UInt8] { Array(Insecure.SHA1.hash(data: d)) }

    struct Item { let rel: [UInt8]; let flags: UInt32; let url: URL; let size: UInt64 }

    static func items(_ folder: URL) -> [Item] {
        var out: [Item] = []
        let fm = FileManager.default
        func walk(_ d: URL) {
            let ents = ((try? fm.contentsOfDirectory(atPath: d.path)) ?? []).filter { !$0.hasPrefix(".") }.sorted { Array($0.utf8).lexicographicallyPrecedes(Array($1.utf8)) }
            var files: [String] = [], dirs: [String] = []
            for e in ents {
                var isDir: ObjCBool = false
                fm.fileExists(atPath: d.appendingPathComponent(e).path, isDirectory: &isDir)
                if isDir.boolValue { dirs.append(e) } else { files.append(e) }
            }
            for e in files + dirs {
                let p = d.appendingPathComponent(e)
                let rel = Array(String(p.path.dropFirst(folder.path.count + 1)).utf8)
                if dirs.contains(e) {
                    out.append(Item(rel: rel, flags: 0x80000004, url: p, size: 0)); walk(p)
                } else {
                    let sz = (try? fm.attributesOfItem(atPath: p.path)[.size] as? UInt64) ?? 0
                    out.append(Item(rel: rel, flags: 0x80000003, url: p, size: sz))
                }
            }
        }
        walk(folder)
        return out
    }

    /// build a debug PKG (installs on HEN/CFW) from a folder
    static func pack(folder: URL, contentID: String, out: URL, progress: ((Double) -> Void)? = nil) throws {
        let items = items(folder)
        let n = items.count
        var off = UInt64(0x20 * n)
        var noffs: [UInt64] = []
        for it in items { noffs.append(off); off += UInt64((it.rel.count + 15) & ~15) }
        var table: [UInt8] = []
        for (it, no) in zip(items, noffs) {
            let isDir = it.flags & 0xFF == 4
            table += be(no, 4); table += be(UInt64(it.rel.count), 4); table += be(off, 8)
            table += be(isDir ? 0 : it.size, 8); table += be(UInt64(it.flags), 4); table += be(0, 4)
            if !isDir { off += (it.size + 15) & ~15 }
        }
        let dataSize = off
        for it in items { table += it.rel + [UInt8](repeating: 0, count: ((it.rel.count + 15) & ~15) - it.rel.count) }

        var cid = Array(contentID.utf8.prefix(0x30)); cid += [UInt8](repeating: 0, count: 0x30 - cid.count)
        var base: [UInt8] = []
        let hv: [(UInt64, Int)] = [(0x7F504B47, 4), (1, 4), (0xC0, 4), (5, 4), (0x80, 4), (UInt64(n), 4), (dataSize + 0x1A0, 8), (0x140, 8), (dataSize, 8)]
        for (v, w) in hv { base += be(v, w) }

        // QA digest: file data, header without content id, descriptors (PSL1GHT order)
        var qd = Insecure.SHA1()
        let total = Double(items.reduce(UInt64(0)) { $0 + $1.size }) * 2 + 1
        var done = 0.0
        for it in items where it.flags & 0xFF != 4 {
            let fh = try FileHandle(forReadingFrom: it.url); defer { try? fh.close() }
            while true {
                let d = fh.readData(ofLength: 8 << 20); if d.isEmpty { break }
                qd.update(data: d); done += Double(d.count); progress?(done / total)
            }
        }
        var hb = base; hb += [UInt8](repeating: 0, count: 0x50)
        qd.update(data: hb)
        qd.update(data: table)
        let qa = Array(Array(qd.finalize()).prefix(16))
        let kl = crypt(qa, [UInt8](repeating: 0, count: 16), start: 0xFFFFFFFFFFFFFFFF)
        var head = base; head += cid; head += qa; head += kl
        var meta: [UInt8] = []
        let mv: [(UInt64, Int)] = [(1, 4), (4, 4), (3, 4), (2, 4), (4, 4), (5, 4), (3, 4), (4, 4), (0xE, 4), (4, 4), (8, 4),
                                   (0, 2), (0, 2), (dataSize, 4), (5, 4), (4, 4), (0x1061, 2), (0, 2)]
        for (v, w) in mv { meta += be(v, w) }
        let hsha = Array(sha1(head)[3..<19]), msha = Array(sha1(meta)[3..<19])
        let pad1 = crypt(msha, [UInt8](repeating: 0, count: 0x30), start: 0)

        FileManager.default.createFile(atPath: out.path, contents: nil)
        let o = try FileHandle(forWritingTo: out); defer { try? o.close() }
        o.write(Data(head)); o.write(Data(hsha)); o.write(Data(crypt(hsha, pad1, start: 0)))
        o.write(Data(meta)); o.write(Data(msha)); o.write(Data(pad1))
        var blk: UInt64 = 0
        var carry: [UInt8] = []
        func emit(_ chunk: [UInt8]) {
            let c = carry + chunk
            let cut = c.count - c.count % 16
            if cut > 0 { o.write(Data(crypt(qa, Array(c[0..<cut]), start: blk))); blk += UInt64(cut / 16) }
            carry = Array(c[cut...])
        }
        emit(table)
        for it in items where it.flags & 0xFF != 4 {
            let fh = try FileHandle(forReadingFrom: it.url); defer { try? fh.close() }
            while true {
                let d = fh.readData(ofLength: 8 << 20); if d.isEmpty { break }
                emit([UInt8](d)); done += Double(d.count); progress?(done / total)
            }
            let pad = Int(((it.size + 15) & ~15) - it.size)
            if pad > 0 { emit([UInt8](repeating: 0, count: pad)) }
        }
        if !carry.isEmpty { o.write(Data(crypt(qa, carry, start: blk))) }
        o.write(Data(count: 0x60))
    }
}

// ================= PARAM.SFO string editing
enum SFOEdit {
    struct Field: Identifiable { var id: String { key }; let key: String; let isInt: Bool; let max: Int; var value: String }

    static func fields(_ d: Data) -> [Field] {
        guard d.count > 20, d.prefix(4) == Data([0, 0x50, 0x53, 0x46]) else { return [] }
        let kt = Int(d.le32(8)), dt = Int(d.le32(12)), n = Int(d.le32(16))
        var out: [Field] = []
        for i in 0..<n {
            let e = 20 + i * 16
            let ko = Int(d.le16(e)), fmt = d.le16(e + 2), ln = Int(d.le32(e + 4)), mx = Int(d.le32(e + 8)), dOff = Int(d.le32(e + 12))
            var kEnd = kt + ko
            while kEnd < d.count && d[kEnd] != 0 { kEnd += 1 }
            let key = String(decoding: d[(kt + ko)..<kEnd], as: UTF8.self)
            let raw = d.subdata(in: (dt + dOff)..<min(d.count, dt + dOff + ln))
            let v = fmt == 0x0404 ? String(raw.le32(0)) : String(decoding: raw.prefix { $0 != 0 }, as: UTF8.self)
            out.append(Field(key: key, isInt: fmt == 0x0404, max: mx, value: v))
        }
        return out
    }

    static func set(_ data: Data, _ key: String, _ value: String) -> Data {
        var d = [UInt8](data)
        let kt = Int(data.le32(8)), dt = Int(data.le32(12)), n = Int(data.le32(16))
        for i in 0..<n {
            let e = 20 + i * 16
            let ko = Int(data.le16(e)), fmt = data.le16(e + 2), mx = Int(data.le32(e + 8)), dOff = Int(data.le32(e + 12))
            var kEnd = kt + ko
            while kEnd < d.count && d[kEnd] != 0 { kEnd += 1 }
            guard String(decoding: d[(kt + ko)..<kEnd], as: UTF8.self) == key else { continue }
            if fmt == 0x0404 {
                let v = UInt32(value) ?? 0
                for j in 0..<4 { d[dt + dOff + j] = UInt8((v >> (8 * UInt32(j))) & 0xFF) }
            } else {
                var raw = Array(value.utf8.prefix(mx - 1)) + [0]
                let ln = raw.count
                raw += [UInt8](repeating: 0, count: mx - raw.count)
                for j in 0..<mx { d[dt + dOff + j] = raw[j] }
                for j in 0..<4 { d[e + 4 + j] = UInt8((UInt32(ln) >> (8 * UInt32(j))) & 0xFF) }
            }
            break
        }
        return Data(d)
    }
}

// ================= edit-mode browser
struct PkgEditView: View {
    @EnvironmentObject var app: AppState
    let root: URL          // extracted folder
    let dir: URL           // folder shown
    let contentID: String
    let pkgName: String
    @State private var entries: [URL] = []
    @State private var adding = false
    @State private var share: URL?
    @State private var renaming: URL?
    @State private var newName = ""

    var body: some View {
        List {
            ForEach(entries, id: \.self) { u in
                let isDir = (try? u.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) ?? false
                Group {
                    if isDir {
                        NavigationLink { PkgEditView(root: root, dir: u, contentID: contentID, pkgName: pkgName) } label: { row(u, true) }
                    } else {
                        NavigationLink { FileEditorView(url: u) } label: { row(u, false) }
                    }
                }
                .contextMenu {
                    if !isDir { Button { share = u } label: { Label("مشاركة / تصدير", systemImage: "square.and.arrow.up") } }
                    Button { renaming = u; newName = u.lastPathComponent } label: { Label("إعادة تسمية", systemImage: "pencil") }
                    Button(role: .destructive) { try? FileManager.default.removeItem(at: u); reload() } label: { Label("حذف", systemImage: "trash") }
                }
            }
        }
        .navigationTitle(dir == root ? "✏ تعديل: \(pkgName)" : dir.lastPathComponent)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            Menu {
                Button { adding = true } label: { Label("إضافة / استبدال ملفات هنا", systemImage: "plus") }
                Button { rebuild() } label: { Label("إعادة البناء إلى PKG", systemImage: "hammer.fill") }
                Button { reload() } label: { Label("تحديث", systemImage: "arrow.clockwise") }
            } label: { Image(systemName: "ellipsis.circle") }
        }
        .docPicker(isPresented: $adding, multiple: true) { urls in
            for u in urls {
                let dst = dir.appendingPathComponent(u.lastPathComponent)
                try? FileManager.default.removeItem(at: dst)
                try? FileManager.default.copyItem(at: u, to: dst)
            }
            reload()
        }
        .alert("إعادة تسمية", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("الاسم", text: $newName)
            Button("حفظ") {
                if let u = renaming, !newName.isEmpty { try? FileManager.default.moveItem(at: u, to: u.deletingLastPathComponent().appendingPathComponent(newName)); reload() }
            }
            Button("إلغاء", role: .cancel) {}
        }
        .sheet(item: Binding(get: { share.map { ShareItem(url: $0) } }, set: { share = $0?.url })) { ShareSheet(url: $0.url) }
        .onAppear { reload() }
    }

    func row(_ u: URL, _ isDir: Bool) -> some View {
        HStack {
            Image(systemName: isDir ? "folder.fill" : FileEditorView.icon(u)).foregroundStyle(isDir ? Color.accentColor : .secondary)
            Text(u.lastPathComponent).environment(\.layoutDirection, .leftToRight)
            Spacer()
            if !isDir, let s = try? u.resourceValues(forKeys: [.fileSizeKey]).fileSize { Text(human(Int64(s))).font(.caption).foregroundStyle(.secondary) }
        }
    }

    func reload() {
        entries = ((try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.isDirectoryKey])) ?? [])
            .filter { !$0.lastPathComponent.hasPrefix(".") }
            .sorted { $0.lastPathComponent.lowercased() < $1.lastPathComponent.lowercased() }
    }

    func rebuild() {
        let built = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("Built")
        try? FileManager.default.createDirectory(at: built, withIntermediateDirectories: true)
        let out = built.appendingPathComponent(pkgName + "_mod.pkg")
        let root = self.root, cid = contentID
        app.run("جاري بناء الحزمة...") { prog in
            try PkgBuilder.pack(folder: root, contentID: cid, out: out) { prog($0) }
            await MainActor.run { share = out }
            return nil
        }
    }
}

// ================= file editors
struct FileEditorView: View {
    @EnvironmentObject var app: AppState
    let url: URL
    @State private var text = ""
    @State private var sfo: [SFOEdit.Field] = []
    @State private var image: UIImage?
    @State private var photo: PhotosPickerItem?
    @State private var hex = ""
    @State private var replacing = false
    @State private var share: URL?

    static func icon(_ u: URL) -> String {
        switch u.pathExtension.lowercased() {
        case "png", "jpg", "jpeg", "gif", "bmp": return "photo"
        case "sfo": return "list.bullet.rectangle"
        case "xml", "txt", "cfg", "ini", "json", "lua", "csv", "html", "conf": return "doc.text"
        case "at3", "wav", "mp3", "ogg": return "music.note"
        case "ttf", "otf": return "textformat"
        default: return "doc"
        }
    }
    var kind: String {
        let e = url.pathExtension.lowercased()
        if url.lastPathComponent.uppercased() == "PARAM.SFO" { return "sfo" }
        if ["png", "jpg", "jpeg", "gif", "bmp"].contains(e) { return "image" }
        if ["xml", "txt", "cfg", "ini", "json", "lua", "csv", "html", "conf", "log"].contains(e) { return "text" }
        return "hex"
    }

    var body: some View {
        Group {
            switch kind {
            case "sfo":
                Form {
                    ForEach($sfo) { $f in
                        VStack(alignment: .leading) {
                            Text(f.key).font(.caption.monospaced()).foregroundStyle(.secondary)
                            TextField(f.key, text: $f.value).keyboardType(f.isInt ? .numberPad : .default)
                        }
                    }
                }
            case "image":
                VStack(spacing: 16) {
                    if let i = image { Image(uiImage: i).resizable().aspectRatio(contentMode: .fit).clipShape(RoundedRectangle(cornerRadius: 10)) }
                    if let i = image { Text("\(Int(i.size.width))×\(Int(i.size.height))").font(.caption).foregroundStyle(.secondary) }
                    PhotosPicker(selection: $photo, matching: .images) { Label("استبدال بصورة من الصور (بنفس المقاس)", systemImage: "photo.on.rectangle") }
                        .buttonStyle(.borderedProminent)
                    Spacer()
                }.padding()
            case "text":
                TextEditor(text: $text).font(.system(.footnote, design: .monospaced)).environment(\.layoutDirection, .leftToRight).padding(4)
            default:
                ScrollView { Text(hex).font(.system(size: 11, design: .monospaced)).environment(\.layoutDirection, .leftToRight).frame(maxWidth: .infinity, alignment: .leading).padding() }
            }
        }
        .navigationTitle(url.lastPathComponent).navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if kind == "sfo" || kind == "text" { Button("حفظ") { save() } }
            Menu {
                Button { replacing = true } label: { Label("استبدال بملف", systemImage: "arrow.triangle.2.circlepath") }
                Button { share = url } label: { Label("مشاركة / تصدير", systemImage: "square.and.arrow.up") }
            } label: { Image(systemName: "ellipsis.circle") }
        }
        .docPicker(isPresented: $replacing) { us in
            if let d = try? Data(contentsOf: us[0]) { try? d.write(to: url); load(); app.message = "✔ تم استبدال الملف" }
        }
        .sheet(item: Binding(get: { share.map { ShareItem(url: $0) } }, set: { share = $0?.url })) { ShareSheet(url: $0.url) }
        .onChange(of: photo) { item in replaceImage(item) }
        .onAppear { load() }
    }

    func load() {
        guard let d = try? Data(contentsOf: url) else { return }
        switch kind {
        case "sfo": sfo = SFOEdit.fields(d)
        case "image": image = UIImage(data: d)
        case "text": text = String(data: d, encoding: .utf8) ?? String(decoding: d, as: UTF8.self)
        default:
            var s = ""
            let bytes = [UInt8](d.prefix(64 * 1024))
            for row in stride(from: 0, to: bytes.count, by: 16) {
                let chunk = bytes[row..<min(row + 16, bytes.count)]
                s += String(format: "%08X  ", row) + chunk.map { String(format: "%02X", $0) }.joined(separator: " ")
                s += "  " + String(chunk.map { $0 >= 32 && $0 < 127 ? Character(Unicode.Scalar($0)) : "." }) + "\n"
            }
            if d.count > bytes.count { s += "\n… (أول 64KB فقط من \(human(Int64(d.count))))" }
            hex = s
        }
    }

    func save() {
        do {
            if kind == "sfo" {
                var d = try Data(contentsOf: url)
                let old = SFOEdit.fields(d)
                for f in sfo where old.first(where: { $0.key == f.key })?.value != f.value { d = SFOEdit.set(d, f.key, f.value) }
                try d.write(to: url)
            } else {
                try Data(text.utf8).write(to: url)
            }
            app.message = "✔ تم الحفظ"
        } catch { app.message = "تعذّر الحفظ: \(error.localizedDescription)" }
    }

    func replaceImage(_ item: PhotosPickerItem?) {
        Task {
            guard let d = try? await item?.loadTransferable(type: Data.self), let img = UIImage(data: d) else { return }
            let target = image?.size ?? img.size
            let f = UIGraphicsImageRendererFormat(); f.scale = 1
            let r = UIGraphicsImageRenderer(size: target, format: f)
            let png = r.pngData { _ in img.draw(in: CGRect(origin: .zero, size: target)) }
            try? png.write(to: url)
            await MainActor.run { image = UIImage(data: png); app.message = "✔ تم استبدال الصورة" }
        }
    }
}
