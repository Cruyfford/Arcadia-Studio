import Foundation
import SwiftUI

/// webMAN MOD on the console: web commands + FTP
struct PS3 {
    let ip: String
    init(ip: String) { self.ip = PS3.cleanIP(ip) }
    static let pkgDir = "/dev_hdd0/packages"

    /// Arabic-Indic / Persian digits and Arabic separators typed on an Arabic keyboard -> 192.168.1.20
    static func cleanIP(_ s: String) -> String {
        var out = ""
        for ch in s.unicodeScalars {
            switch ch.value {
            case 0x0660...0x0669: out.unicodeScalars.append(UnicodeScalar(ch.value - 0x0660 + 0x30)!)
            case 0x06F0...0x06F9: out.unicodeScalars.append(UnicodeScalar(ch.value - 0x06F0 + 0x30)!)
            case 0x066B, 0x066C, 0x060C, 0x2C: out.append(".")          // ٫ ٬ ، ,
            case 0x30...0x39, 0x2E: out.unicodeScalars.append(ch)
            default: break
            }
        }
        return out
    }

    func web(_ path: String) async throws -> Data {
        let enc = path.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? path
        guard let u = URL(string: "http://\(ip)\(enc)") else { throw FTPError.failed("عنوان غير صالح") }
        var r = URLRequest(url: u); r.timeoutInterval = 10
        let (d, _) = try await URLSession.shared.data(for: r)
        return d
    }

    func ftp() async throws -> FTPClient {
        let f = FTPClient(host: ip); try await f.connect(); return f
    }

    func test() async -> (Bool, Bool) {
        var ftpOK = false, webOK = false
        if let f = try? await ftp() { ftpOK = true; f.close() }
        if (try? await web("/popup.ps3/Arcadia Studio")) != nil { webOK = true }
        return (ftpOK, webOK)
    }

    func notify(_ text: String) async { _ = try? await web("/popup.ps3/" + text) }
    func install(_ remote: String) async throws { _ = try await web("/install.ps3" + remote) }
    func remoteDownload(_ url: String, to dir: String = "/dev_hdd0/packages") async throws {
        _ = try await web("/download.ps3?to=\(dir)&url=\(url)")
    }

    /// send changed store files straight into the installed store (no reinstall)
    func sync(_ store: StoreProject, progress: @escaping (Int, Int) -> Void) async throws -> Int {
        let files = store.changed.sorted().compactMap { rel -> (String, Data)? in
            guard let d = store.fileData(rel) else { return nil }; return (rel, d)
        }
        let f = try await ftp(); defer { f.close() }
        for (i, (rel, d)) in files.enumerated() {
            let remote = STORE_PREFIX + rel
            try await f.mkdirs((remote as NSString).deletingLastPathComponent)
            try await f.upload(d, to: remote)
            progress(i + 1, files.count)
        }
        await notify("ARCADIA updated")
        return files.count
    }

    /// game folder (title id from the content id) and installed size of a PKG
    static func pkgInfo(_ u: URL) -> (title: String, size: Int64)? {
        let scoped = u.startAccessingSecurityScopedResource(); defer { if scoped { u.stopAccessingSecurityScopedResource() } }
        guard let r = try? PkgReader(u) else { return nil }
        let cid = r.contentID
        guard cid.count >= 16 else { return nil }
        let title = String(Array(cid)[7..<16])
        let size = r.entries.filter { !$0.isDir }.reduce(Int64(0)) { $0 + Int64($1.size) }
        return (title, size)
    }

    /// recursive size of a folder over FTP (stops at the deadline)
    func folderSize(_ f: FTPClient, _ path: String, until: Date) async -> Int64 {
        guard Date() < until, let items = try? await f.list(path) else { return 0 }
        var total: Int64 = 0
        for e in items where e.name != "." && e.name != ".." {
            if e.isDir { total += await folderSize(f, path + "/" + e.name, until: until) } else { total += e.size }
        }
        return total
    }

    /// bytes now in the game folder + any installer temp folders (_INST_*)
    func installedBytes(_ f: FTPClient, title: String) async -> (Int64, Bool) {
        let until = Date().addingTimeInterval(8)
        var total = await folderSize(f, "/dev_hdd0/game/" + title, until: until)
        var temp = false
        if let g = try? await f.list("/dev_hdd0/game") {
            for e in g where e.isDir && e.name.hasPrefix("_INST_") {
                temp = true; total += await folderSize(f, "/dev_hdd0/game/" + e.name, until: until)
            }
        }
        return (total, temp)
    }

    /// follow the install on the console by watching the game folder grow (approximate)
    func watchInstall(title: String, expected: Int64, baseline: Int64,
                      progress: @escaping (Double, Int64) -> Void) async -> Bool {
        guard expected > 0, let f = try? await ftp() else { return false }
        defer { f.close() }
        let start = Date(); var last: Int64 = -1; var lastChange = Date(); var sawTemp = false
        while Date().timeIntervalSince(start) < 3600 {
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            let (now, temp) = await installedBytes(f, title: title)
            if temp { sawTemp = true }
            let grown = max(0, now - baseline)
            progress(min(Double(grown) / Double(expected), 0.99), grown)
            if now != last { last = now; lastChange = Date() }
            let idle = Date().timeIntervalSince(lastChange)
            if !temp && grown >= expected * 95 / 100 { return true }
            if !temp && sawTemp && idle > 6 { return true }                 // temp folder moved into place
            if grown > 0 && !temp && idle > 25 { return true }              // stopped growing
            if grown == 0 && !temp && Date().timeIntervalSince(start) > 120 { return false }  // nothing seen
        }
        return false
    }

    func sendPKG(_ local: URL, install: Bool, progress: @escaping (Int64, Int64) -> Void) async throws -> String {
        let remote = PS3.pkgDir + "/" + local.lastPathComponent.replacingOccurrences(of: " ", with: "_")
        let f = try await ftp(); defer { f.close() }
        try await f.mkdirs(PS3.pkgDir)
        let scoped = local.startAccessingSecurityScopedResource()
        defer { if scoped { local.stopAccessingSecurityScopedResource() } }
        try await f.upload(file: local, to: remote, progress: progress)
        if install { try await self.install(remote) }
        return remote
    }
}

func human(_ n: Int64) -> String {
    var v = Double(n)
    for u in ["B", "KB", "MB", "GB"] {
        if v < 1024 || u == "GB" { return u == "B" ? "\(n) B" : String(format: "%.1f %@", v, u) }
        v /= 1024
    }
    return "\(n)"
}
