import SwiftUI

/// Hex editor like the Windows Studio: whole file, go to address, search (text or hex), write bytes,
/// find & replace, edit any row, save.
struct HexEditorView: View {
    @EnvironmentObject var app: AppState
    let url: URL
    @State private var bytes: [UInt8] = []
    @State private var dirty = false
    @State private var hit: Int?            // highlighted offset
    @State private var hitLen = 0
    @State private var editRow: Int?
    @State private var rowText = ""
    @State private var askGoto = false
    @State private var askFind = false
    @State private var askPatch = false
    @State private var askReplace = false
    @State private var f1 = ""
    @State private var f2 = ""
    @State private var scrollTo: Int?

    var rows: Int { (bytes.count + 15) / 16 }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(0..<rows, id: \.self) { r in
                        rowView(r).id(r)
                            .background(isHitRow(r) ? Color.accentColor.opacity(0.35) : (r % 2 == 0 ? Color.clear : Color.white.opacity(0.03)))
                            .onTapGesture { editRow = r; rowText = hexRow(r) }
                    }
                }.padding(.horizontal, 6)
            }
            .onChange(of: scrollTo) { v in if let v = v { withAnimation { proxy.scrollTo(v, anchor: .center) } } }
        }
        .environment(\.layoutDirection, .leftToRight)
        .navigationTitle(url.lastPathComponent).navigationBarTitleDisplayMode(.inline)
        .safeAreaInset(edge: .bottom) {
            HStack {
                Text("\(human(Int64(bytes.count)))  •  اضغط على أي سطر لتعديله").font(.caption)
                Spacer()
                if dirty { Text("● غير محفوظ").font(.caption).foregroundStyle(.orange) }
            }.padding(8).background(.ultraThinMaterial)
        }
        .toolbar {
            if dirty { Button("حفظ") { save() } }
            Menu {
                Button { f1 = ""; askGoto = true } label: { Label("اذهب للعنوان", systemImage: "arrow.right.to.line") }
                Button { f1 = ""; askFind = true } label: { Label("بحث (نص أو hex)", systemImage: "magnifyingglass") }
                Button { if hit != nil { findNext(from: (hit ?? 0) + 1) } } label: { Label("البحث التالي", systemImage: "chevron.down") }
                Button { f1 = ""; f2 = ""; askPatch = true } label: { Label("كتابة بايتات عند عنوان", systemImage: "pencil") }
                Button { f1 = ""; f2 = ""; askReplace = true } label: { Label("بحث واستبدال الكل", systemImage: "arrow.2.squarepath") }
                NavigationLink { StringsToolView(url: url) } label: { Label("🔤 تعريب: نصوص هذا الملف", systemImage: "character.book.closed") }
                Divider()
                Button { save() } label: { Label("حفظ", systemImage: "square.and.arrow.down") }
            } label: { Image(systemName: "ellipsis.circle") }
        }
        .alert("اذهب للعنوان (hex)", isPresented: $askGoto) {
            TextField("مثال 1A2B0", text: $f1)
            Button("اذهب") { if let o = Int(f1.replacingOccurrences(of: "0x", with: ""), radix: 16), o < bytes.count { hit = o; hitLen = 1; scrollTo = o / 16 } }
            Button("إلغاء", role: .cancel) {}
        }
        .alert("بحث", isPresented: $askFind) {
            TextField("نص، أو hex مثل 7F454C46", text: $f1)
            Button("بحث") { findNext(from: 0) }
            Button("إلغاء", role: .cancel) {}
        }
        .alert("كتابة بايتات", isPresented: $askPatch) {
            TextField("العنوان (hex)", text: $f1)
            TextField("البايتات (hex) مثل 00 01 FF", text: $f2)
            Button("كتابة") { patch() }
            Button("إلغاء", role: .cancel) {}
        }
        .alert("بحث واستبدال الكل", isPresented: $askReplace) {
            TextField("ابحث عن (نص أو hex)", text: $f1)
            TextField("استبدل بـ (بنفس الطول)", text: $f2)
            Button("استبدال") { replaceAll() }
            Button("إلغاء", role: .cancel) {}
        }
        .sheet(item: Binding(get: { editRow.map { RowID(r: $0) } }, set: { editRow = $0?.r })) { rid in
            NavigationStack {
                Form {
                    Section(String(format: "السطر %08X — 16 بايت", rid.r * 16)) {
                        TextField("hex", text: $rowText, axis: .vertical).font(.system(.body, design: .monospaced))
                            .textInputAutocapitalization(.characters).autocorrectionDisabled()
                    }
                    Section { Text(asciiRow(rid.r)).font(.system(.body, design: .monospaced)) }
                }
                .environment(\.layoutDirection, .leftToRight)
                .toolbar {
                    ToolbarItem(placement: .confirmationAction) { Button("تطبيق") { applyRow(rid.r); editRow = nil } }
                    ToolbarItem(placement: .cancellationAction) { Button("إلغاء") { editRow = nil } }
                }
            }.presentationDetents([.medium])
        }
        .onAppear { load() }
    }

    struct RowID: Identifiable { let r: Int; var id: Int { r } }

    func isHitRow(_ r: Int) -> Bool {
        guard let h = hit else { return false }
        return r * 16 < h + max(hitLen, 1) && h < r * 16 + 16
    }
    func hexRow(_ r: Int) -> String {
        bytes[(r * 16)..<min(r * 16 + 16, bytes.count)].map { String(format: "%02X", $0) }.joined(separator: " ")
    }
    func asciiRow(_ r: Int) -> String {
        String(bytes[(r * 16)..<min(r * 16 + 16, bytes.count)].map { $0 >= 32 && $0 < 127 ? Character(Unicode.Scalar($0)) : "." })
    }
    func rowView(_ r: Int) -> some View {
        HStack(spacing: 8) {
            Text(String(format: "%08X", r * 16)).foregroundStyle(.secondary)
            Text(hexRow(r))
            Text(asciiRow(r)).foregroundStyle(.secondary)
        }
        .font(.system(size: 10.5, design: .monospaced)).lineLimit(1).padding(.vertical, 2)
    }

    func load() { if let d = try? Data(contentsOf: url) { bytes = [UInt8](d) } }

    func parse(_ s: String) -> [UInt8]? {
        let h = s.replacingOccurrences(of: " ", with: "").replacingOccurrences(of: "0x", with: "")
        if !h.isEmpty, h.count % 2 == 0, h.allSatisfy({ $0.isHexDigit }) {
            var out: [UInt8] = []; var i = h.startIndex
            while i < h.endIndex { let j = h.index(i, offsetBy: 2); out.append(UInt8(h[i..<j], radix: 16)!); i = j }
            return out
        }
        return nil
    }
    func needle(_ s: String) -> [UInt8] { parse(s) ?? Array(s.utf8) }

    func find(_ n: [UInt8], from: Int) -> Int? {
        guard !n.isEmpty, n.count <= bytes.count else { return nil }
        var i = max(0, from)
        while i <= bytes.count - n.count {
            if bytes[i] == n[0] && Array(bytes[i..<(i + n.count)]) == n { return i }
            i += 1
        }
        return nil
    }

    func findNext(from: Int) {
        let n = needle(f1)
        if let i = find(n, from: from) ?? find(n, from: 0) { hit = i; hitLen = n.count; scrollTo = i / 16 }
        else { app.message = "لا توجد نتائج" }
    }

    func patch() {
        guard let o = Int(f1.replacingOccurrences(of: "0x", with: ""), radix: 16), let b = parse(f2), o + b.count <= bytes.count else {
            app.message = "عنوان أو بايتات غير صالحة"; return
        }
        for (k, v) in b.enumerated() { bytes[o + k] = v }
        dirty = true; hit = o; hitLen = b.count; scrollTo = o / 16
    }

    func replaceAll() {
        let a = needle(f1), b = needle(f2)
        guard !a.isEmpty, a.count == b.count else { app.message = "لازم يكون الاستبدال بنفس الطول (\(a.count) بايت)"; return }
        var i = 0, n = 0
        while let p = find(a, from: i) { for k in 0..<b.count { bytes[p + k] = b[k] }; n += 1; i = p + a.count }
        if n > 0 { dirty = true }
        app.message = "تم استبدال \(n) موضع"
    }

    func applyRow(_ r: Int) {
        guard let b = parse(rowText) else { app.message = "اكتب البايتات بصيغة hex"; return }
        let start = r * 16, len = min(16, bytes.count - start)
        guard b.count == len else { app.message = "السطر لازم يكون \(len) بايت"; return }
        for k in 0..<len { bytes[start + k] = b[k] }
        dirty = true
    }

    func save() {
        do {
            let bak = url.appendingPathExtension("bak")
            if !FileManager.default.fileExists(atPath: bak.path) { try? FileManager.default.copyItem(at: url, to: bak) }
            try Data(bytes).write(to: url); dirty = false
            app.message = "✔ تم الحفظ (نسخة احتياطية: \(bak.lastPathComponent))"
        } catch { app.message = "تعذّر الحفظ: \(error.localizedDescription)" }
    }
}
