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
