import Foundation
import SwiftUI
import ZIPFoundation

let ROOT_XML = "USRDIR/VIDEOGAMESSCZ_xmb.xml"
let ROOT_VIEW = "VIDEOGAMESSCZ_xmb_main"
let STORE_PREFIX = "/dev_hdd0/game/VIDESTORE/"
let FOLDER = "type:x-xmb/folder-pixmap"
let ACTION = "type:x-xmb/module-action"
let WEBMAN_DL = "http://127.0.0.1/download.ps3?url="

// ---------- .arcadia document (same JSON as the Windows Studio)
struct XView: Codable { var id: String; var tables: [String: [String: String]]; var items: [[String: String]] }
struct XFile: Codable { var enc: String?; var views: [XView] }
struct ProjectDoc: Codable {
    var format: String
    var format_version: Int?
    var name: String?
    var saved: String?
    var store_version: Int?
    var sidecar: [String: String]
    var texts: [String: String]?
    var pending: [String]?
    var files: [String]?
    var xml: [String: XFile]
}

struct Target: Hashable { let file: String; let view: String }

struct Node: Identifiable, Hashable {
    var id: String { file + "#" + view + "#" + key }
    let key: String, kind: String, cls: String, src: String?
    let pairs: [String: String]          // XML-unescaped XMB strings
    let target: Target?
    let file: String, view: String
    var isFolder: Bool { cls == FOLDER && target != nil }
    var link: String? {
        for k in ["pkg_src", "module_action"] {
            guard let v = pairs[k]?.trimmingCharacters(in: .whitespaces), !v.isEmpty else { continue }
            if let r = v.range(of: "url=") , v.hasPrefix("http://127.0.0.1") { return String(v[r.upperBound...]) }
            if v.hasPrefix("http"), !v.contains("127.0.0.1"), !v.contains("localhost") { return v }
        }
        return nil
    }
    var method: Int {
        if pairs["pkg_src"] != nil { return 2 }
        return (pairs["module_action"] ?? "").hasPrefix(WEBMAN_DL) ? 0 : 1
    }
}

enum XMLText {
    static func unescape(_ s: String) -> String {
        guard s.contains("&") else { return s }
        var out = s.replacingOccurrences(of: "&lt;", with: "<").replacingOccurrences(of: "&gt;", with: ">")
            .replacingOccurrences(of: "&quot;", with: "\"").replacingOccurrences(of: "&apos;", with: "'")
        // numeric entities
        let re = try! NSRegularExpression(pattern: "&#(x?)([0-9A-Fa-f]+);")
        let ns = out as NSString
        var res = "", last = 0
        for m in re.matches(in: out, range: NSRange(location: 0, length: ns.length)) {
            res += ns.substring(with: NSRange(location: last, length: m.range.location - last))
            let hex = ns.substring(with: m.range(at: 1)) == "x"
            let num = ns.substring(with: m.range(at: 2))
            if let v = UInt32(num, radix: hex ? 16 : 10), let sc = Unicode.Scalar(v) { res.unicodeScalars.append(sc) }
            last = m.range.location + m.range.length
        }
        res += ns.substring(from: last)
        out = res
        return out.replacingOccurrences(of: "&amp;", with: "&")
    }
    static func escape(_ s: String) -> String {
        s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;").replacingOccurrences(of: ">", with: "&gt;")
    }
}

final class StoreProject: ObservableObject {
    @Published var doc: ProjectDoc
    @Published var rev = 0                     // bumps on every change so views refresh
    var thumbs: [String: Data] = [:]           // rel path -> small preview image
    var files: [String: Data] = [:]            // rel path -> full files you added
    var url: URL?
    var changed: Set<String>
    private var imgCache: [String: UIImage] = [:]

    init(doc: ProjectDoc, url: URL?) {
        self.doc = doc; self.url = url; self.changed = Set(doc.pending ?? [])
    }

    // ---------- load / save
    static func load(_ url: URL) throws -> StoreProject {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let data = try Data(contentsOf: url)
        if data.starts(with: [0x50, 0x4B]) {
            let arc = try Archive(data: data, accessMode: .read)
            var json = Data(); var thumbs: [String: Data] = [:]; var files: [String: Data] = [:]
            for e in arc where e.type == .file {
                var d = Data()
                _ = try arc.extract(e, skipCRC32: true) { d.append($0) }
                if e.path == "store.json" { json = d }
                else if e.path.hasPrefix("thumbs/") { thumbs[String(e.path.dropFirst(7).dropLast(5))] = d }
                else if e.path.hasPrefix("files/") { files[String(e.path.dropFirst(6))] = d }
            }
            let p = StoreProject(doc: try JSONDecoder().decode(ProjectDoc.self, from: json), url: url)
            p.thumbs = thumbs; p.files = files
            return p
        }
        return StoreProject(doc: try JSONDecoder().decode(ProjectDoc.self, from: data), url: url)
    }

    func encodedJSON() throws -> Data {
        doc.pending = Array(changed).sorted()
        doc.files = Array(files.keys).sorted()
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd HH:mm"; doc.saved = f.string(from: Date())
        return try JSONEncoder().encode(doc)
    }

    /// writes the project (.arcadia zip or plain .json) and keeps a history copy
    func save(to target: URL? = nil) throws {
        guard let dest = target ?? url else { throw NSError(domain: "Arcadia", code: 1, userInfo: [NSLocalizedDescriptionKey: "لا يوجد ملف للحفظ"]) }
        let json = try encodedJSON()
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + "." + dest.pathExtension)
        if dest.pathExtension.lowercased() == "json" {
            try json.write(to: tmp)
        } else {
            let arc = try Archive(url: tmp, accessMode: .create)
            func put(_ path: String, _ d: Data, _ comp: CompressionMethod) throws {
                try arc.addEntry(with: path, type: .file, uncompressedSize: Int64(d.count), compressionMethod: comp) { pos, size in
                    d.subdata(in: Int(pos)..<Int(pos) + size)
                }
            }
            try put("store.json", json, .deflate)
            for (rel, d) in files { try put("files/" + rel, d, .none) }
            for (rel, d) in thumbs { try put("thumbs/" + rel + ".webp", d, .none) }
        }
        let scoped = dest.startAccessingSecurityScopedResource()
        defer { if scoped { dest.stopAccessingSecurityScopedResource() } }
        let data = try Data(contentsOf: tmp)
        try data.write(to: dest, options: .atomic)
        try? FileManager.default.removeItem(at: tmp)
        url = dest
        History.snapshot(json, name: doc.name ?? "ARCADIA")
    }

    func touch() { rev += 1 }

    // ---------- reading
    func viewIndex(_ rel: String, _ vid: String) -> Int? { doc.xml[rel]?.views.firstIndex { $0.id == vid } }

    func resolve(_ rel: String, _ src: String?) -> Target? {
        guard let src = src, !src.isEmpty else { return nil }
        if src.hasPrefix("#") { return Target(file: rel, view: String(src.dropFirst())) }
        if src.hasPrefix("xmb://localhost/"), let hash = src.firstIndex(of: "#") {
            let path = String(src[src.index(src.startIndex, offsetBy: 15)..<hash])
            let view = String(src[src.index(after: hash)...])
            if path.hasPrefix(STORE_PREFIX) {
                let r = String(path.dropFirst(STORE_PREFIX.count))
                if doc.xml[r] != nil { return Target(file: r, view: view) }
            }
        }
        return nil
    }

    func children(_ t: Target) -> [Node] {
        guard let vi = viewIndex(t.file, t.view), let v = doc.xml[t.file]?.views[vi] else { return [] }
        return v.items.map { a in
            let key = a["key"] ?? ""
            var pairs: [String: String] = [:]
            for (k, val) in v.tables[key] ?? [:] { pairs[k] = XMLText.unescape(val) }
            return Node(key: key, kind: a["_"] ?? "Query", cls: a["class"] ?? "", src: a["src"], pairs: pairs,
                        target: resolve(t.file, a["src"]), file: t.file, view: t.view)
        }
    }
    var root: Target { Target(file: ROOT_XML, view: ROOT_VIEW) }

    func display(_ s: String) -> String { doc.sidecar[s] ?? Arabic.fromXMB(s) }
    func title(_ n: Node) -> String { display(n.pairs["title"] ?? n.key) }
    func info(_ n: Node) -> String { display(n.pairs["info"] ?? "") }

    func relOf(_ ps3: String) -> String? { ps3.hasPrefix(STORE_PREFIX) ? String(ps3.dropFirst(STORE_PREFIX.count)) : nil }

    func image(_ ps3: String?) -> UIImage? {
        guard let p = ps3, let rel = relOf(p) else { return nil }
        if let c = imgCache[rel] { return c }
        guard let d = files[rel] ?? thumbs[rel], let img = UIImage(data: d) else { return nil }
        imgCache[rel] = img; return img
    }

    // ---------- editing (same operations as the Windows Studio)
    private func mutateView(_ rel: String, _ vid: String, _ fn: (inout XView) -> Void) {
        guard let vi = viewIndex(rel, vid) else { return }
        fn(&doc.xml[rel]!.views[vi])
        changed.insert(rel); touch()
    }

    func setPair(_ n: Node, _ pkey: String, _ value: String, arabic: Bool = true) {
        setPair(n.file, n.view, n.key, pkey, value, arabic: arabic)
    }

    func setPair(_ rel: String, _ vid: String, _ key: String, _ pkey: String, _ value: String, arabic: Bool = true) {
        let disp = arabic ? Arabic.toXMB(value) : value
        if disp != value { doc.sidecar[disp] = value; changed.insert("USRDIR/ARCADIA_SRC.json") }
        mutateView(rel, vid) { v in
            var t = v.tables[key] ?? [:]
            if value.isEmpty { t.removeValue(forKey: pkey) } else { t[pkey] = XMLText.escape(disp) }
            v.tables[key] = t
        }
    }

    @discardableResult
    func addNode(_ t: Target, key base: String, pairs: [String: String], cls: String = ACTION, src: String? = nil) -> String {
        let existing = Set(children(t).map { $0.key })
        var key = base, n = 2
        while existing.contains(key) { key = "\(base)_\(n)"; n += 1 }
        var table: [String: String] = [:]
        for (k, v) in pairs {
            let disp = (k == "title" || k == "info") ? Arabic.toXMB(v) : v
            if disp != v { doc.sidecar[disp] = v }
            table[k] = XMLText.escape(disp)
        }
        mutateView(t.file, t.view) { v in
            v.tables[key] = table
            var item = ["_": "Query", "class": cls, "key": key, "attr": key]
            if let s = src { item["src"] = s }
            v.items.append(item)
        }
        return key
    }

    func removeNode(_ n: Node) {
        mutateView(n.file, n.view) { v in
            v.tables.removeValue(forKey: n.key)
            if let i = v.items.firstIndex(where: { $0["key"] == n.key }) { v.items.remove(at: i) }
        }
    }

    func moveNode(_ n: Node, _ delta: Int) {
        mutateView(n.file, n.view) { v in
            guard let i = v.items.firstIndex(where: { $0["key"] == n.key }) else { return }
            let j = i + delta
            guard j >= 0, j < v.items.count else { return }
            v.items.swapAt(i, j)
        }
    }

    func moveNodes(_ t: Target, from: IndexSet, to: Int) {
        mutateView(t.file, t.view) { v in v.items.move(fromOffsets: from, toOffset: to) }
    }

    func addSection(in t: Target, title: String, info: String) {
        let vid = "arc_sec_\(Int(Date().timeIntervalSince1970))"
        doc.xml[t.file]?.views.append(XView(id: vid, tables: [:], items: []))
        let bg = children(t).compactMap { $0.pairs["pic"] }.first
        var pairs = ["icon": STORE_PREFIX + "USRDIR/XMB/VIDEOGAMESSCZ.png", "title": title, "info": info]
        if let bg = bg { pairs["pic"] = bg }
        addNode(t, key: vid, pairs: pairs, cls: FOLDER, src: "#" + vid)
    }

    func addGame(in t: Target, title: String, info: String, link: String, method: Int, icon: String? = nil, pic: String? = nil) {
        var pairs = ["title": title, "info": info, "icon": icon ?? STORE_PREFIX + "USRDIR/XMB/PS3.png"]
        if let p = pic ?? children(t).compactMap({ $0.pairs["pic"] }).first { pairs["pic"] = p }
        let key = addNode(t, key: "arc_game_\(Int(Date().timeIntervalSince1970) % 1000000)", pairs: pairs)
        if !link.isEmpty { setLink(t.file, t.view, key, link, method) }
    }

    func setLink(_ rel: String, _ vid: String, _ key: String, _ link: String, _ method: Int) {
        if method == 2 {
            setPair(rel, vid, key, "pkg_src", link, arabic: false); setPair(rel, vid, key, "pkg_src_qa", link, arabic: false); return
        }
        setPair(rel, vid, key, "module_name", method == 0 ? "webbrowser_plugin" : "webrender_plugin", arabic: false)
        setPair(rel, vid, key, "module_action", method == 0 ? WEBMAN_DL + link : link, arabic: false)
        setPair(rel, vid, key, "ingame", "disable", arabic: false)
    }

    /// add a picture you chose; returns its PS3 path
    func addImage(_ img: UIImage, folder: String, name: String, maxSize: CGSize) -> String {
        let scale = min(1, min(maxSize.width / img.size.width, maxSize.height / img.size.height))
        let size = CGSize(width: floor(img.size.width * scale), height: floor(img.size.height * scale))
        let r = UIGraphicsImageRenderer(size: size, format: { let f = UIGraphicsImageRendererFormat(); f.scale = 1; return f }())
        let png = r.pngData { _ in img.draw(in: CGRect(origin: .zero, size: size)) }
        let rel = "USRDIR/ARCADIA/\(folder)/\(name).png"
        files[rel] = png; imgCache[rel] = nil; changed.insert(rel); touch()
        return STORE_PREFIX + rel
    }

    // ---------- every section (for pickers)
    func allSections() -> [(String, Target)] {
        var out: [(String, Target)] = [("🏠 القائمة الرئيسية", root)]
        var seen: Set<Target> = [root]
        func walk(_ t: Target, _ prefix: String, _ depth: Int) {
            guard depth < 6 else { return }
            for n in children(t) where n.isFolder {
                guard let tt = n.target, !seen.contains(tt) else { continue }
                seen.insert(tt)
                let name = prefix.isEmpty ? title(n) : prefix + " › " + title(n)
                out.append((name, tt)); walk(tt, name, depth + 1)
            }
        }
        walk(root, "", 0)
        return out
    }

    // ---------- generate the XML the PS3 reads (same layout as the Windows Studio)
    func xmlData(_ rel: String) -> Data? {
        guard let f = doc.xml[rel] else { return nil }
        var s = "<XMBML version=\"1.0\">\n"
        for v in f.views {
            s += "\t<View id=\"\(v.id)\">\n\t\t<Attributes>\n"
            for (k, pairs) in v.tables.sorted(by: { $0.key < $1.key }) {
                s += "\t\t\t<Table key=\"\(k)\">\n"
                for (pk, pv) in pairs.sorted(by: { $0.key < $1.key }) { s += "\t\t\t\t<Pair key=\"\(pk)\"><String>\(pv)</String></Pair>\n" }
                s += "\t\t\t</Table>\n"
            }
            s += "\t\t</Attributes>\n\t\t<Items>\n"
            for it in v.items {
                let kind = it["_"] ?? "Query"
                let order = ["class", "key", "attr", "src"]
                let keys = order.filter { it[$0] != nil } + it.keys.filter { $0 != "_" && !order.contains($0) }.sorted()
                s += "\t\t\t<\(kind) " + keys.map { "\($0)=\"\(it[$0]!)\"" }.joined(separator: " ") + "/>\n"
            }
            s += "\t\t</Items>\n\t</View>\n"
        }
        s += "</XMBML>\n"
        if (f.enc ?? "utf-8") == "latin-1", let d = s.data(using: .isoLatin1) { return d }
        return s.data(using: .utf8)
    }

    /// bytes of a changed file, for sending straight to the console
    func fileData(_ rel: String) -> Data? {
        if rel.lowercased().hasSuffix(".xml") { return xmlData(rel) }
        if rel == "USRDIR/ARCADIA_SRC.json" { return try? JSONSerialization.data(withJSONObject: doc.sidecar) }
        if let t = doc.texts?[rel] { return t.data(using: .utf8) }
        return files[rel]
    }
}

/// every save keeps a copy of the store (last 30)
enum History {
    static var dir: URL {
        let d = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("History")
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }
    static func snapshot(_ json: Data, name: String) {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd_HH-mm-ss"
        try? json.write(to: dir.appendingPathComponent("\(name)_\(f.string(from: Date())).json"))
        let all = list()
        for u in all.dropFirst(30) { try? FileManager.default.removeItem(at: u) }
    }
    static func list() -> [URL] {
        let fm = FileManager.default
        let urls = (try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.contentModificationDateKey])) ?? []
        return urls.filter { $0.pathExtension == "json" }.sorted { a, b in
            let da = (try? a.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            let db = (try? b.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            return da > db
        }
    }
}
