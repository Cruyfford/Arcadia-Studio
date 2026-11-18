import SwiftUI
import PhotosUI
import UniformTypeIdentifiers

// ================= store home
struct StoreHome: View {
    @EnvironmentObject var app: AppState
    @State private var importing = false
    @State private var history = false
    @State private var xmb = false

    var body: some View {
        NavigationStack {
            Group {
                if let s = app.store {
                    SectionView(store: s, target: s.root, title: s.doc.name ?? "ARCADIA")
                } else {
                    VStack(spacing: 18) {
                        Image(systemName: "storefront").font(.system(size: 60)).foregroundStyle(.tint)
                        Text("افتح مشروع المتجر").font(.title2.bold())
                        Text("ملف .arcadia أو .json اللي حفظته من Arcadia Studio على الكمبيوتر (ملف ← حفظ كمشروع صغير)")
                            .multilineTextAlignment(.center).foregroundStyle(.secondary).padding(.horizontal)
                        Button { importing = true } label: { Label("فتح مشروع", systemImage: "folder") }.buttonStyle(.borderedProminent)
                    }
                }
            }
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Menu {
                        Button { importing = true } label: { Label("فتح مشروع...", systemImage: "folder") }
                        if app.store != nil {
                            Button { app.save() } label: { Label("حفظ", systemImage: "square.and.arrow.down") }
                            if let u = app.store?.url { ShareLink(item: u) { Label("مشاركة / تصدير الملف", systemImage: "square.and.arrow.up") } }
                            Button { xmb = true } label: { Label("معاينة شكل الجهاز", systemImage: "tv") }
                            Button { history = true } label: { Label("سجل النسخ", systemImage: "clock.arrow.circlepath") }
                            Button { sync() } label: { Label("إرسال التعديلات للجهاز", systemImage: "bolt.fill") }
                        }
                    } label: { Image(systemName: "ellipsis.circle") }
                }
            }
            .fileImporter(isPresented: $importing, allowedContentTypes: [.arcadia, .json, .zip, .data]) { r in
                if case .success(let u) = r { app.open(u) }
            }
            .sheet(isPresented: $history) { HistoryView() }
            .fullScreenCover(isPresented: $xmb) { if let s = app.store { XMBPreview(store: s) } }
        }
    }

    func sync() {
        guard let s = app.store else { return }
        let ip = UserDefaults.standard.string(forKey: "ps3_ip") ?? ""
        guard !ip.isEmpty else { app.message = "اكتب IP الجهاز في تبويب «الجهاز» أولاً"; return }
        if s.changed.isEmpty { app.message = "لا توجد تعديلات جديدة لإرسالها"; return }
        app.save()
        app.run("جاري إرسال التعديلات إلى الجهاز...") { prog in
            let n = try await PS3(ip: ip).sync(s) { a, b in prog(Double(a) / Double(max(b, 1))) }
            await MainActor.run { s.changed.removeAll(); try? s.save() }
            return "تم إرسال \(n) ملف إلى المتجر في الجهاز.\nاخرج من قسم المتجر وادخله من جديد لتظهر التعديلات."
        }
    }
}

// ================= a section (folder) of the store
struct SectionView: View {
    @ObservedObject var store: StoreProject
    let target: Target
    let title: String
    @State private var editing: Node?
    @State private var addGame = false
    @State private var addSection = false
    @State private var confirmDelete: Node?

    var body: some View {
        let nodes = store.children(target)
        List {
            if let first = nodes.first(where: { $0.pairs["pic"] != nil }), let bg = store.image(first.pairs["pic"]) {
                Image(uiImage: bg).resizable().aspectRatio(16 / 9, contentMode: .fill)
                    .frame(maxHeight: 150).clipShape(RoundedRectangle(cornerRadius: 14)).listRowInsets(EdgeInsets()).padding(.vertical, 4)
            }
            ForEach(nodes) { n in
                row(n)
                    .contextMenu { menu(n, nodes) }
                    .swipeActions(edge: .trailing) {
                        Button(role: .destructive) { confirmDelete = n } label: { Label("حذف", systemImage: "trash") }
                        Button { editing = n } label: { Label("تعديل", systemImage: "pencil") }.tint(.indigo)
                    }
            }
            .onMove { store.moveNodes(target, from: $0, to: $1) }
        }
        .id(store.rev)
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItemGroup(placement: .navigationBarTrailing) {
                Menu {
                    Button { addGame = true } label: { Label("إضافة لعبة هنا", systemImage: "plus") }
                    Button { addSection = true } label: { Label("قسم جديد هنا", systemImage: "folder.badge.plus") }
                } label: { Image(systemName: "plus.circle.fill") }
                EditButton()
            }
        }
        .sheet(item: $editing) { NodeEditor(store: store, node: $0) }
        .sheet(isPresented: $addGame) { AddSheet(store: store, target: target, section: false) }
        .sheet(isPresented: $addSection) { AddSheet(store: store, target: target, section: true) }
        .confirmationDialog("حذف «\(confirmDelete.map { store.title($0) } ?? "")» من القائمة؟", isPresented: Binding(get: { confirmDelete != nil }, set: { if !$0 { confirmDelete = nil } }), titleVisibility: .visible) {
            Button("حذف", role: .destructive) { if let n = confirmDelete { store.removeNode(n) }; confirmDelete = nil }
        }
    }

    @ViewBuilder func row(_ n: Node) -> some View {
        let content = HStack(spacing: 12) {
            Group {
                if let img = store.image(n.pairs["icon"]) { Image(uiImage: img).resizable().aspectRatio(contentMode: .fit) }
                else { Image(systemName: n.isFolder ? "folder.fill" : "gamecontroller.fill").foregroundStyle(.tint) }
            }.frame(width: 64, height: 36)
            VStack(alignment: .leading, spacing: 2) {
                Text(store.title(n)).font(.body.weight(.semibold)).lineLimit(2)
                let info = store.info(n)
                if !info.isEmpty { Text(info).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
            }
            Spacer()
            if n.link != nil { Image(systemName: "arrow.down.circle").foregroundStyle(.secondary) }
        }
        if n.isFolder, let t = n.target {
            NavigationLink { SectionView(store: store, target: t, title: store.title(n)) } label: { content }
        } else {
            Button { editing = n } label: { content }.buttonStyle(.plain)
        }
    }

    @ViewBuilder func menu(_ n: Node, _ nodes: [Node]) -> some View {
        Button { editing = n } label: { Label("تعديل", systemImage: "pencil") }
        if let u = n.link {
            Button { Downloads.shared.add(u, connections: UserDefaults.standard.integer(forKey: "dl_conns").clamped(1, 16, def: 8)) } label: { Label("تنزيل على الجوال", systemImage: "arrow.down.circle") }
            Button { UIPasteboard.general.string = u } label: { Label("نسخ الرابط", systemImage: "doc.on.doc") }
            Button { sendToPS3(u) } label: { Label("تنزيل مباشرة في الجهاز", systemImage: "gamecontroller") }
        }
        Divider()
        Button { store.moveNode(n, -1) } label: { Label("نقل لأعلى", systemImage: "arrow.up") }
        Button { store.moveNode(n, 1) } label: { Label("نقل لأسفل", systemImage: "arrow.down") }
        Divider()
        Button(role: .destructive) { confirmDelete = n } label: { Label("حذف", systemImage: "trash") }
    }

    func sendToPS3(_ url: String) {
        let ip = UserDefaults.standard.string(forKey: "ps3_ip") ?? ""
        guard !ip.isEmpty else { return }
        Task { try? await PS3(ip: ip).remoteDownload(url) }
    }
}

extension Int {
    func clamped(_ lo: Int, _ hi: Int, def: Int) -> Int { self == 0 ? def : Swift.min(hi, Swift.max(lo, self)) }
}

// ================= edit one item (changes apply immediately)
struct NodeEditor: View {
    @ObservedObject var store: StoreProject
    let node: Node
    @Environment(\.dismiss) var dismiss
    @State private var title = ""
    @State private var info = ""
    @State private var link = ""
    @State private var method = 0
    @State private var iconItem: PhotosPickerItem?
    @State private var picItem: PhotosPickerItem?
    @State private var icon: String?
    @State private var pic: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("العنوان والوصف") {
                    TextField("العنوان", text: $title)
                    TextField("الوصف", text: $info)
                }
                Section("الصور (اضغط للتغيير)") {
                    HStack(spacing: 16) {
                        PhotosPicker(selection: $iconItem, matching: .images) { imgBox(icon, 120, 66, "الأيقونة") }
                        PhotosPicker(selection: $picItem, matching: .images) { imgBox(pic, 140, 79, "الخلفية") }
                    }
                }
                if !node.isFolder {
                    Section("التنزيل") {
                        TextField("رابط ملف PKG", text: $link).keyboardType(.URL).textInputAutocapitalization(.never).environment(\.layoutDirection, .leftToRight)
                        Picker("الطريقة", selection: $method) {
                            Text("webMAN — في الخلفية (موصى به)").tag(0)
                            Text("متصفح PS3").tag(1)
                            Text("متجر PSN (pkg_src)").tag(2)
                        }
                    }
                }
                Section { Text(node.key).font(.caption.monospaced()).foregroundStyle(.secondary) }
            }
            .navigationTitle("تعديل").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("تم") { apply(); dismiss() } }
                ToolbarItem(placement: .cancellationAction) { Button("إلغاء") { dismiss() } }
            }
            .onAppear {
                title = store.title(node); info = store.info(node)
                link = node.link ?? ""; method = node.method
                icon = node.pairs["icon"]; pic = node.pairs["pic"]
            }
            .onChange(of: iconItem) { item in load(item, folder: "ICONS", size: CGSize(width: 320, height: 176)) { icon = $0 } }
            .onChange(of: picItem) { item in load(item, folder: "BG", size: CGSize(width: 1920, height: 1080)) { pic = $0 } }
        }
    }

    func imgBox(_ ps3: String?, _ w: CGFloat, _ h: CGFloat, _ label: String) -> some View {
        VStack {
            ZStack {
                RoundedRectangle(cornerRadius: 8).fill(Color.black.opacity(0.4))
                if let img = store.image(ps3) { Image(uiImage: img).resizable().aspectRatio(contentMode: .fit) }
                else { Image(systemName: "photo").foregroundStyle(.secondary) }
            }.frame(width: w, height: h)
            Text(label).font(.caption)
        }
    }

    func load(_ item: PhotosPickerItem?, folder: String, size: CGSize, done: @escaping (String) -> Void) {
        Task {
            guard let d = try? await item?.loadTransferable(type: Data.self), let img = UIImage(data: d) else { return }
            await MainActor.run {
                let name = "\(node.key)_\(Int(Date().timeIntervalSince1970))"
                done(store.addImage(img, folder: folder, name: name, maxSize: size))
            }
        }
    }

    func apply() {
        if title != store.title(node) { store.setPair(node, "title", title) }
        if info != store.info(node) { store.setPair(node, "info", info) }
        if let i = icon, i != node.pairs["icon"] { store.setPair(node, "icon", i, arabic: false) }
        if let p = pic, p != node.pairs["pic"] { store.setPair(node, "pic", p, arabic: false) }
        let l = link.trimmingCharacters(in: .whitespaces)
        if !node.isFolder, !l.isEmpty, l != (node.link ?? "") || method != node.method {
            store.setLink(node.file, node.view, node.key, l, method)
        }
    }
}

// ================= add a game or a section
struct AddSheet: View {
    @ObservedObject var store: StoreProject
    let target: Target
    let section: Bool
    @Environment(\.dismiss) var dismiss
    @State private var title = ""
    @State private var info = ""
    @State private var link = ""
    @State private var method = 0
    @State private var dest: Target?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("القسم", selection: Binding(get: { dest ?? target }, set: { dest = $0 })) {
                        ForEach(store.allSections().map { SecItem(name: $0.0, target: $0.1) }) { Text($0.name).tag($0.target) }
                    }
                }
                Section {
                    TextField(section ? "اسم القسم" : "اسم اللعبة", text: $title)
                    TextField("الوصف", text: $info)
                }
                if !section {
                    Section("التنزيل") {
                        TextField("رابط ملف PKG", text: $link).keyboardType(.URL).textInputAutocapitalization(.never).environment(\.layoutDirection, .leftToRight)
                        Picker("الطريقة", selection: $method) {
                            Text("webMAN (موصى به)").tag(0); Text("متصفح PS3").tag(1); Text("متجر PSN").tag(2)
                        }
                    }
                }
            }
            .navigationTitle(section ? "قسم جديد" : "إضافة لعبة").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("إضافة") {
                        let t = dest ?? target
                        if section { store.addSection(in: t, title: title, info: info) }
                        else { store.addGame(in: t, title: title, info: info, link: link, method: method) }
                        dismiss()
                    }.disabled(title.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                ToolbarItem(placement: .cancellationAction) { Button("إلغاء") { dismiss() } }
            }
            .onAppear { if !section, let s = UIPasteboard.general.string, s.hasPrefix("http") { link = s } }
        }
    }
}

struct SecItem: Identifiable { let name: String; let target: Target; var id: Target { target } }

// ================= history
struct HistoryView: View {
    @EnvironmentObject var app: AppState
    @Environment(\.dismiss) var dismiss
    @State private var items: [URL] = []
    var body: some View {
        NavigationStack {
            List(items, id: \.self) { u in
                Button {
                    guard let s = app.store, let d = try? Data(contentsOf: u), let doc = try? JSONDecoder().decode(ProjectDoc.self, from: d) else { return }
                    s.doc = doc; s.changed.formUnion(doc.xml.keys); s.touch()
                    app.message = "↩ تم استرجاع النسخة — احفظ لتثبيتها"; dismiss()
                } label: {
                    VStack(alignment: .leading) {
                        Text(u.deletingPathExtension().lastPathComponent).font(.body.monospaced())
                        Text("اضغط للاسترجاع").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("سجل النسخ").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("إغلاق") { dismiss() } } }
            .onAppear { items = History.list() }
            .overlay { if items.isEmpty { Text("كل حفظ يضيف نسخة هنا").foregroundStyle(.secondary) } }
        }
    }
}

// ================= XMB-style preview (raw XMB strings, like the console draws them)
struct XMBPreview: View {
    @ObservedObject var store: StoreProject
    @Environment(\.dismiss) var dismiss
    @State private var cat = 0
    @State private var stack: [Node] = []
    @State private var sel: [Target: Int] = [:]

    var body: some View {
        let roots = store.children(store.root)
        let lv = level(roots)
        let nodes = lv.nodes, idx = lv.index, parent = lv.parent
        let cur = nodes.indices.contains(idx) ? nodes[idx] : parent
        GeometryReader { geo in
            ZStack(alignment: .topLeading) {
                background(cur, parent).frame(width: geo.size.width, height: geo.size.height).clipped()
                VStack(alignment: .leading, spacing: 18) {
                    HStack { Spacer(); Text(Date(), style: .time).font(.headline).foregroundStyle(.white) }
                    if stack.isEmpty {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 22) {
                                ForEach(Array(roots.enumerated()), id: \.offset) { i, n in
                                    VStack {
                                        icon(n, h: i == cat ? 64 : 40).opacity(i == cat ? 1 : 0.6)
                                        if i == cat { raw(store, n, "title", 15) }
                                    }.onTapGesture { cat = i }
                                }
                            }.padding(.horizontal, 30)
                        }
                    } else if let p = stack.last {
                        HStack { icon(p, h: 44); raw(store, p, "title", 15).opacity(0.8) }.padding(.leading, 20)
                    }
                    ScrollView {
                        VStack(alignment: .leading, spacing: 14) {
                            ForEach(Array(nodes.enumerated()), id: \.offset) { i, n in
                                HStack(spacing: 16) {
                                    icon(n, h: i == idx ? 70 : 46).frame(width: 130)
                                    VStack(alignment: .leading, spacing: 4) {
                                        raw(store, n, "title", i == idx ? 20 : 16)
                                        if i == idx, n.pairs["info"] != nil { raw(store, n, "info", 13).opacity(0.85) }
                                    }
                                }
                                .opacity(i == idx ? 1 : 0.7)
                                .onTapGesture {
                                    if i == idx, n.target != nil { enter(n) } else { select(i) }
                                }
                            }
                        }.padding(.horizontal, 30)
                    }
                }
                .padding(.top, 20)
                .environment(\.layoutDirection, .leftToRight)
                VStack {
                    Spacer()
                    HStack {
                        Button { if !stack.isEmpty { stack.removeLast() } else { dismiss() } } label: {
                            Label(stack.isEmpty ? "إغلاق" : "رجوع (◯)", systemImage: stack.isEmpty ? "xmark" : "chevron.backward")
                        }.buttonStyle(.borderedProminent)
                        Spacer()
                        Text("اضغط مرتين على قسم للدخول").font(.caption).foregroundStyle(.white.opacity(0.7))
                    }.padding()
                }
            }
        }
        .ignoresSafeArea()
        .background(Color.black)
    }

    struct Level { let nodes: [Node]; let index: Int; let parent: Node? }
    func level(_ roots: [Node]) -> Level {
        if let p = stack.last, let t = p.target {
            return Level(nodes: store.children(t), index: sel[t] ?? 0, parent: p)
        }
        guard roots.indices.contains(cat), let t = roots[cat].target else { return Level(nodes: [], index: 0, parent: nil) }
        return Level(nodes: store.children(t), index: sel[t] ?? 0, parent: roots[cat])
    }
    func currentTarget() -> Target? {
        if let p = stack.last { return p.target }
        let roots = store.children(store.root)
        return roots.indices.contains(cat) ? roots[cat].target : nil
    }
    func select(_ i: Int) { if let t = currentTarget() { sel[t] = i } }
    func enter(_ n: Node) { stack.append(n) }

    @ViewBuilder func background(_ n: Node?, _ p: Node?) -> some View {
        if let img = store.image(n?.pairs["pic"]) ?? store.image(p?.pairs["pic"]) {
            Image(uiImage: img).resizable().aspectRatio(contentMode: .fill)
        } else {
            LinearGradient(colors: [Color(red: 0.04, green: 0.1, blue: 0.29), Color(red: 0.23, green: 0.06, blue: 0.38)], startPoint: .top, endPoint: .bottom)
        }
    }
    @ViewBuilder func icon(_ n: Node, h: CGFloat) -> some View {
        if let img = store.image(n.pairs["icon"]) { Image(uiImage: img).resizable().aspectRatio(contentMode: .fit).frame(height: h) }
        else { Image(systemName: n.isFolder ? "folder.fill" : "gamecontroller.fill").font(.system(size: h * 0.5)).foregroundStyle(.white).frame(height: h) }
    }
}

/// draw the stored XMB string exactly in its stored order (left-to-right override), like the console
func raw(_ store: StoreProject, _ n: Node, _ key: String, _ size: CGFloat) -> some View {
    let s = n.pairs[key] ?? (key == "title" ? n.key : "")
    return Text(verbatim: "\u{202D}" + s + "\u{202C}")
        .font(.custom("IBMPlexSansArabic-SemiBold", size: size))
        .foregroundStyle(.white).shadow(color: .black.opacity(0.6), radius: 2, x: 1, y: 1)
}
