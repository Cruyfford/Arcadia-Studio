import Foundation
import SwiftUI

// ================= link checker
enum LinkStatus: String { case ok, broken, unsure, pending }

struct LinkItem: Identifiable {
    let id = UUID()
    let node: Node, title: String, url: String
    var status: LinkStatus = .pending, note = "", size: Int64? = nil
}

enum LinkChecker {
    static let ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) ArcadiaStudio/1.0"

    static func collect(_ store: StoreProject, in target: Target?) -> [LinkItem] {
        var out: [LinkItem] = []
        var seen: Set<Target> = []
        func walk(_ t: Target, _ depth: Int) {
            guard depth < 10, !seen.contains(t) else { return }
            seen.insert(t)
            for n in store.children(t) {
                if let u = n.link { out.append(LinkItem(node: n, title: store.title(n), url: u)) }
                if let tt = n.target { walk(tt, depth + 1) }
            }
        }
        walk(target ?? store.root, 0)
        return out
    }

    static func check(_ url: String) async -> (LinkStatus, String, Int64?) {
        guard let u = URL(string: url) else { return (.broken, "رابط غير صالح", nil) }
        let cfg = URLSessionConfiguration.ephemeral; cfg.timeoutIntervalForRequest = 15
        let s = URLSession(configuration: cfg)
        do {
            if url.contains("mediafire.com") {
                var r = URLRequest(url: u); r.setValue(ua, forHTTPHeaderField: "User-Agent")
                let (d, resp) = try await s.data(for: r)
                let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
                let body = String(decoding: d.prefix(200_000), as: UTF8.self)
                let final = resp.url?.absoluteString ?? ""
                if code >= 400 || final.contains("error.php") || body.contains("File Removed") || body.contains("file_removed") {
                    return (.broken, "الملف محذوف من MediaFire", nil)
                }
                if body.contains("aria-label=\"Download file\"") || body.contains("downloadButton") {
                    if let r = body.range(of: #"Download \(([\d.,]+\s*[KMG]B)\)"#, options: .regularExpression) {
                        return (.ok, "MediaFire " + body[r].dropFirst(10).dropLast(1), nil)
                    }
                    return (.ok, "MediaFire", nil)
                }
                return (.unsure, "صفحة MediaFire بدون زر تنزيل واضح", nil)
            }
            var r = URLRequest(url: u); r.httpMethod = "HEAD"; r.setValue(ua, forHTTPHeaderField: "User-Agent")
            var (_, resp) = try await s.data(for: r)
            var h = resp as? HTTPURLResponse
            if let c = h?.statusCode, [400, 403, 405, 501].contains(c) || c >= 500 {
                var g = URLRequest(url: u); g.setValue("bytes=0-0", forHTTPHeaderField: "Range"); g.setValue(ua, forHTTPHeaderField: "User-Agent")
                (_, resp) = try await s.data(for: g); h = resp as? HTTPURLResponse
            }
            let code = h?.statusCode ?? 0
            var size: Int64? = nil
            if let cr = h?.value(forHTTPHeaderField: "Content-Range"), let t = cr.split(separator: "/").last { size = Int64(t) }
            if size == nil, let cl = h?.value(forHTTPHeaderField: "Content-Length") { size = Int64(cl) }
            if code == 404 || code == 410 { return (.broken, "غير موجود (\(code))", nil) }
            if code >= 400 { return (code < 500 ? .broken : .unsure, "خطأ من الخادم \(code)", size) }
            let ctype = h?.value(forHTTPHeaderField: "Content-Type") ?? ""
            if url.lowercased().hasSuffix(".pkg") && ctype.contains("text/html") && (size ?? 0) < 200_000 {
                return (.unsure, "يرجع صفحة ويب بدل الملف", size)
            }
            if size == 0 { return (.broken, "ملف فارغ", 0) }
            return (.ok, "", size)
        } catch let e as URLError where e.code == .timedOut {
            return (.unsure, "انتهت المهلة (الخادم بطيء)", nil)
        } catch let e as URLError where e.code == .cannotFindHost || e.code == .cannotConnectToHost {
            return (.broken, "تعذّر الاتصال بالخادم", nil)
        } catch {
            return (.unsure, String(error.localizedDescription.prefix(120)), nil)
        }
    }

    /// resolve a MediaFire page to the direct file link
    static func resolve(_ url: String) async -> String {
        guard url.contains("mediafire.com/file"), let u = URL(string: url) else { return url }
        var r = URLRequest(url: u); r.setValue(ua, forHTTPHeaderField: "User-Agent")
        guard let res = try? await URLSession.shared.data(for: r) else { return url }
        let body = String(decoding: res.0, as: UTF8.self)
        if let m = body.range(of: #"https://download[0-9]*\.mediafire\.com/[^"' ]+"#, options: .regularExpression) { return String(body[m]) }
        return url
    }
}

// ================= fast downloads (several connections per file, pause/resume)
final class DownloadTask: NSObject, ObservableObject, Identifiable, URLSessionDataDelegate {
    let id = UUID()
    let url: String
    @Published var name: String
    @Published var total: Int64 = 0
    @Published var done: Int64 = 0
    @Published var state = "⏳ تجهيز"
    @Published var speed: Double = 0
    var file: URL?
    private var segs: [(start: Int64, end: Int64, pos: Int64)] = []
    private var tasks: [Int: Int] = [:]            // taskIdentifier -> segment
    private var session: URLSession?
    private var fh: FileHandle?
    private let q = DispatchQueue(label: "dl")
    private var lastTick = Date(), lastDone: Int64 = 0
    let connections: Int

    init(url: String, connections: Int) {
        self.url = url; self.connections = max(1, connections)
        name = URL(string: url)?.lastPathComponent.removingPercentEncoding ?? "file"
    }

    static var folder: URL {
        let d = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("Downloads")
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }

    func start() {
        Task {
            let direct = await LinkChecker.resolve(url)
            guard let u = URL(string: direct) else { await set("❌ رابط غير صالح"); return }
            var r = URLRequest(url: u); r.httpMethod = "HEAD"
            let resp = try? await URLSession.shared.data(for: r).1 as? HTTPURLResponse
            let size = Int64(resp?.value(forHTTPHeaderField: "Content-Length") ?? "") ?? 0
            let ranges = (resp?.value(forHTTPHeaderField: "Accept-Ranges") ?? "").contains("bytes") && size > 4_000_000
            let fname = resp?.suggestedFilename ?? u.lastPathComponent
            let dst = DownloadTask.folder.appendingPathComponent(fname.isEmpty ? name : fname)
            await MainActor.run { self.name = dst.lastPathComponent; self.total = size; self.file = dst }
            FileManager.default.createFile(atPath: dst.path, contents: nil)
            fh = try? FileHandle(forWritingTo: dst)
            let n = ranges ? connections : 1
            let part = size > 0 ? size / Int64(n) : 0
            segs = (0..<n).map { i -> (start: Int64, end: Int64, pos: Int64) in
                let s = Int64(i) * part
                let e = (i == n - 1 || size == 0) ? max(size - 1, -1) : s + part - 1
                return (s, e, s)
            }
            resume(direct: u)
        }
    }

    private var directURL: URL?
    private func resume(direct: URL? = nil) {
        if let d = direct { directURL = d }
        guard let u = directURL else { return }
        let cfg = URLSessionConfiguration.default; cfg.httpMaximumConnectionsPerHost = connections
        session = URLSession(configuration: cfg, delegate: self, delegateQueue: nil)
        for (i, s) in segs.enumerated() where s.end < 0 || s.pos <= s.end {
            var r = URLRequest(url: u)
            if s.end >= 0 && (segs.count > 1 || s.pos > 0) { r.setValue("bytes=\(s.pos)-\(s.end)", forHTTPHeaderField: "Range") }
            let t = session!.dataTask(with: r); tasks[t.taskIdentifier] = i; t.resume()
        }
        Task { await set("⬇ جاري التنزيل") }
    }

    func pause() { session?.invalidateAndCancel(); session = nil; Task { await set("⏸ متوقف") } }
    func resumeDownload() { resume() }
    func cancel() { session?.invalidateAndCancel(); session = nil; try? fh?.close(); if let f = file { try? FileManager.default.removeItem(at: f) }; Task { await set("✖ أُلغي") } }

    @MainActor private func set(_ s: String) { state = s }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        q.sync {
            guard let i = tasks[dataTask.taskIdentifier], let fh = fh else { return }
            try? fh.seek(toOffset: UInt64(segs[i].pos))
            fh.write(data)
            segs[i].pos += Int64(data.count)
        }
        let got = q.sync { segs.reduce(Int64(0)) { $0 + ($1.pos - $1.start) } }
        let now = Date()
        if now.timeIntervalSince(lastTick) > 0.5 {
            let sp = Double(got - lastDone) / now.timeIntervalSince(lastTick)
            lastTick = now; lastDone = got
            DispatchQueue.main.async { self.done = got; self.speed = sp }
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let finished: Bool = q.sync {
            tasks.removeValue(forKey: task.taskIdentifier)
            return tasks.isEmpty
        }
        if let e = error as? URLError, e.code == .cancelled { return }
        if let e = error { Task { await set("⚠ انقطع: \(e.localizedDescription) — اضغط مطوّلاً ← استئناف") }; return }
        if let h = task.response as? HTTPURLResponse, h.statusCode >= 400 {
            Task { await set("❌ خطأ من الخادم \(h.statusCode)") }; return
        }
        if finished {
            try? fh?.close()
            let got = q.sync { segs.reduce(Int64(0)) { $0 + ($1.pos - $1.start) } }
            DispatchQueue.main.async { self.done = got; if self.total == 0 { self.total = got }; self.state = "✅ اكتمل"; self.speed = 0 }
        }
    }
}

final class Downloads: ObservableObject {
    static let shared = Downloads()
    @Published var items: [DownloadTask] = []
    func add(_ url: String, connections: Int) {
        let d = DownloadTask(url: url, connections: connections); items.insert(d, at: 0); d.start()
    }
}
