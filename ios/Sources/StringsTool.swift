import SwiftUI

/// Arabization tool (same as the Windows Studio): list the texts inside any game file, type the Arabic,
/// write it back in place — joined letters, right-to-left order applied, within the original space.
struct TextEntry: Identifiable {
    let id: Int          // offset
    let utf16: Bool
    let raw: Int         // original byte length
    let max: Int         // bytes available (original + free zeros after it)
    let text: String
    var tr = ""
}

enum TextScan {
    private static func isLetter(_ s: Unicode.Scalar) -> Bool { s.properties.isAlphabetic }

    static func scan(_ b: [UInt8], minLen: Int = 4) -> [TextEntry] {
        var out: [TextEntry] = []
        var covered: [(Int, Int)] = []
        // UTF-8 / ASCII runs
        var i = 0
        while i < b.count {
            let start = i
            var scalars: [Unicode.Scalar] = []
            while i < b.count {
                let c = b[i]
                var len = 0
                if (c >= 0x20 && c < 0x7F) || c == 0x09 || c == 0x0A || c == 0x0D { len = 1 }
                else if c >= 0xC2 && c <= 0xDF { len = 2 } else if c >= 0xE0 && c <= 0xEF { len = 3 } else if c >= 0xF0 && c <= 0xF4 { len = 4 }
                if len == 0 || i + len > b.count { break }
                if len > 1 && !(1..<len).allSatisfy({ b[i + $0] & 0xC0 == 0x80 }) { break }
                guard let s = String(bytes: b[i..<(i + len)], encoding: .utf8)?.unicodeScalars.first else { break }
                scalars.append(s); i += len
            }
            if scalars.count >= minLen, scalars.filter(isLetter).count >= 2 {
                let raw = i - start
                var z = 0
                while i + z < b.count && b[i + z] == 0 && z < 4096 { z += 1 }
                let t = String(String.UnicodeScalarView(scalars))
                out.append(TextEntry(id: start, utf16: false, raw: raw, max: raw + Swift.max(0, z - 1), text: t))
                covered.append((start, i))
            }
            if i == start { i += 1 }
        }
        // UTF-16LE runs (even offsets, outside the UTF-8 ones)
        var cov = 0
        var j = 0
        while j + 1 < b.count {
            while cov < covered.count && covered[cov].1 <= j { cov += 1 }
            if cov < covered.count && covered[cov].0 <= j { j = covered[cov].1 + (covered[cov].1 & 1); continue }
            let start = j
            var units: [UInt16] = []
            while j + 1 < b.count {
                let lo = b[j], hi = b[j + 1]
                let ok = (hi == 0 && ((lo >= 0x20 && lo < 0x7F) || lo == 0x0A || lo == 0x0D || lo == 0x09)) || hi == 0x06 || (hi >= 0xFB && hi <= 0xFE)
                if !ok { break }
                if cov < covered.count && covered[cov].0 <= j { break }
                units.append(UInt16(lo) | UInt16(hi) << 8); j += 2
            }
            if units.count >= minLen {
                let t = String(decoding: units, as: UTF16.self)
                if t.unicodeScalars.filter(isLetter).count >= 2 {
                    var z = 0
                    while j + z + 1 < b.count && b[j + z] == 0 && b[j + z + 1] == 0 && z < 4096 { z += 2 }
                    out.append(TextEntry(id: start, utf16: true, raw: j - start, max: j - start + Swift.max(0, z - 2), text: t))
                }
            }
            if j == start { j += 2 }
        }
        return out.sorted { $0.id < $1.id }
    }

    static func encode(_ e: TextEntry, shape: Bool, visual: Bool) -> [UInt8] {
        var t = e.tr
        if Arabic.hasArabic(t) {
            if shape && visual { t = Arabic.toXMB(t) } else if shape { t = Arabic.reshape(t) }
        }
        return e.utf16 ? t.utf16.flatMap { [UInt8($0 & 0xFF), UInt8($0 >> 8)] } : Array(t.utf8)
    }

    static func inject(_ b: inout [UInt8], _ entries: [TextEntry], shape: Bool, visual: Bool, spaces: Bool) -> (Int, Int) {
        var n = 0, skipped = 0
        for e in entries where !e.tr.isEmpty {
            var enc = encode(e, shape: shape, visual: visual)
            if enc.count > e.max { skipped += 1; continue }
            if spaces && e.raw > enc.count {
                let fill = e.raw - enc.count
                let pad: [UInt8] = (0..<fill).map { k in (e.utf16 && k % 2 == 1) ? UInt8(0) : UInt8(0x20) }
                enc += pad
            }
            for k in 0..<e.max { b[e.id + k] = k < enc.count ? enc[k] : 0 }
            n += 1
        }
        return (n, skipped)
    }

    static func csv(_ entries: [TextEntry]) -> String {
        func q(_ s: String) -> String { "\"" + s.replacingOccurrences(of: "\"", with: "\"\"") + "\"" }
        var s = "offset,encoding,max_bytes,original,translation\n"
        for e in entries { s += "0x\(String(e.id, radix: 16, uppercase: true)),\(e.utf16 ? "utf-16-le" : "utf-8"),\(e.max),\(q(e.text)),\(q(e.tr))\n" }
        return s
    }

    /// minimal CSV reader (quoted fields, "" escapes, newlines inside quotes)
    static func parseCSV(_ s: String) -> [[String]] {
        var rows: [[String]] = [], row: [String] = [], field = "", quoted = false
        var it = Array(s.replacingOccurrences(of: "\r\n", with: "\n")), i = 0
        if it.first == "\u{FEFF}" { it.removeFirst() }
        while i < it.count {
            let c = it[i]
            if quoted {
                if c == "\"" { if i + 1 < it.count && it[i + 1] == "\"" { field.append("\""); i += 1 } else { quoted = false } }
                else { field.append(c) }
            } else if c == "\"" { quoted = true }
            else if c == "," { row.append(field); field = "" }
            else if c == "\n" { row.append(field); rows.append(row); row = []; field = "" }
            else { field.append(c) }
            i += 1
        }
        if !field.isEmpty || !row.isEmpty { row.append(field); rows.append(row) }
        return rows
    }
}

struct StringsToolView: View {
    @EnvironmentObject var app: AppState
    let url: URL
    @State private var bytes: [UInt8] = []
    @State private var entries: [TextEntry] = []
    @State private var query = ""
    @State private var onlyTr = false
    @State private var shape = true
    @State private var visual = true
    @State private var spaces = false
    @State private var share: URL?
    @State private var importing = false
    @State private var loading = true

    var shown: [Int] {
        entries.indices.filter { i in
            let e = entries[i]
            if onlyTr && e.tr.isEmpty { return false }
            if query.isEmpty { return true }
            return e.text.localizedCaseInsensitiveContains(query) || e.tr.localizedCaseInsensitiveContains(query)
        }
    }

    var body: some View {
        List {
            Section {
                Toggle("ربط الحروف العربية", isOn: $shape)
                Toggle("عكس الاتجاه (للألعاب اللي ما تدعم العربي)", isOn: $visual)
                Toggle("تعبئة الباقي بمسافات بدل أصفار", isOn: $spaces)
                Toggle("المترجم فقط", isOn: $onlyTr)
                let tr = entries.filter { !$0.tr.isEmpty }
                let bad = tr.filter { TextScan.encode($0, shape: shape, visual: visual).count > $0.max }.count
                Text("النصوص: \(entries.count) • مترجم: \(tr.count) • أطول من المساحة: \(bad)").font(.caption)
            }
            ForEach(shown.prefix(3000), id: \.self) { i in
                let e = entries[i]
                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text(String(format: "0x%X", e.id)).font(.caption2.monospaced()).foregroundStyle(.secondary)
                        Text(e.utf16 ? "UTF-16" : "UTF-8").font(.caption2).foregroundStyle(.secondary)
                        Spacer()
                        if !e.tr.isEmpty {
                            let n = TextScan.encode(e, shape: shape, visual: visual).count
                            Text("\(n) / \(e.max)").font(.caption2.bold()).foregroundStyle(n <= e.max ? .green : .red)
                        } else { Text("المساحة \(e.max)").font(.caption2).foregroundStyle(.secondary) }
                    }
                    Text(e.text).font(.callout).environment(\.layoutDirection, .leftToRight).textSelection(.enabled)
                    TextField("الترجمة", text: Binding(get: { entries[i].tr }, set: { entries[i].tr = $0 }), axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                }.padding(.vertical, 2)
            }
            if shown.count > 3000 { Text("يظهر أول 3000 — استخدم البحث للباقي").font(.caption).foregroundStyle(.secondary) }
        }
        .searchable(text: $query, prompt: "بحث في النصوص")
        .overlay { if loading { ProgressView("جاري استخراج النصوص...") } }
        .navigationTitle("🔤 تعريب: " + url.lastPathComponent).navigationBarTitleDisplayMode(.inline)
        .toolbar {
            Button("حفظ") { save() }
            Menu {
                Button { exportCSV() } label: { Label("تصدير CSV (للترجمة في Excel)", systemImage: "square.and.arrow.up") }
                Button { importing = true } label: { Label("استيراد ترجمة من CSV", systemImage: "square.and.arrow.down") }
                Button { for i in shown { entries[i].tr = "" } } label: { Label("مسح ترجمة الظاهر", systemImage: "xmark") }
            } label: { Image(systemName: "ellipsis.circle") }
        }
        .docPicker(isPresented: $importing) { us in importCSV(us[0]) }
        .sheet(item: Binding(get: { share.map { ShareItem(url: $0) } }, set: { share = $0?.url })) { ShareSheet(url: $0.url) }
        .task {
            let u = url
            let r: ([UInt8], [TextEntry]) = await Task.detached {
                let b = [UInt8]((try? Data(contentsOf: u)) ?? Data())
                return (b, TextScan.scan(b))
            }.value
            bytes = r.0; entries = r.1; loading = false
        }
    }

    func save() {
        var b = bytes
        let (n, skipped) = TextScan.inject(&b, entries, shape: shape, visual: visual, spaces: spaces)
        guard n > 0 else { app.message = skipped > 0 ? "كل الترجمات أطول من المساحة — اختصرها" : "اكتب ترجمة أولاً"; return }
        let bak = url.appendingPathExtension("bak")
        if !FileManager.default.fileExists(atPath: bak.path) { try? FileManager.default.copyItem(at: url, to: bak) }
        do {
            try Data(b).write(to: url); bytes = b
            app.message = "تمت كتابة \(n) ترجمة في الملف." + (skipped > 0 ? "\nلم تُكتب \(skipped) لأنها أطول من المساحة." : "") + "\nنسخة احتياطية: \(bak.lastPathComponent)"
        } catch { app.message = "تعذّر الحفظ: \(error.localizedDescription)" }
    }

    func exportCSV() {
        let out = FileManager.default.temporaryDirectory.appendingPathComponent(url.deletingPathExtension().lastPathComponent + "_texts.csv")
        try? Data(("\u{FEFF}" + TextScan.csv(entries)).utf8).write(to: out)
        share = out
    }

    func importCSV(_ u: URL) {
        guard let s = try? String(contentsOf: u, encoding: .utf8) else { app.message = "تعذّر قراءة الملف"; return }
        var byOff: [Int: Int] = [:]
        for (i, e) in entries.enumerated() { byOff[e.id] = i }
        var n = 0
        for row in TextScan.parseCSV(s).dropFirst() where row.count >= 5 {
            let o = row[0].replacingOccurrences(of: "0x", with: "")
            if let off = Int(o, radix: 16), let i = byOff[off] { entries[i].tr = row[4]; n += 1 }
        }
        app.message = "تم استيراد \(n) ترجمة"
    }
}
