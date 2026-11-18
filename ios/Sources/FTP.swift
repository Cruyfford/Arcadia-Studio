import Foundation
import Network

struct FTPEntry: Identifiable, Hashable {
    var id: String { name }
    let name: String, isDir: Bool, size: Int64, date: String
}

enum FTPError: LocalizedError {
    case failed(String)
    var errorDescription: String? { if case .failed(let s) = self { return s }; return nil }
}

/// Small FTP client (webMAN's FTP server on the PS3), built on Network.framework.
final class FTPClient {
    let host: String, port: UInt16
    private var ctrl: NWConnection?
    private var buffer = Data()
    private let q = DispatchQueue(label: "ftp")

    init(host: String, port: UInt16 = 21) { self.host = host; self.port = port }

    // ---------- connection helpers
    private static func open(_ host: String, _ port: UInt16, queue: DispatchQueue) async throws -> NWConnection {
        let c = NWConnection(host: NWEndpoint.Host(host), port: NWEndpoint.Port(rawValue: port)!, using: .tcp)
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            var done = false
            c.stateUpdateHandler = { st in
                guard !done else { return }
                switch st {
                case .ready: done = true; cont.resume()
                case .failed(let e): done = true; cont.resume(throwing: e)
                case .waiting(let e): done = true; c.cancel(); cont.resume(throwing: e)
                default: break
                }
            }
            c.start(queue: queue)
            queue.asyncAfter(deadline: .now() + 10) {
                if !done { done = true; c.cancel(); cont.resume(throwing: FTPError.failed("انتهت مهلة الاتصال بالجهاز \(host)")) }
            }
        }
        return c
    }

    private static func receive(_ c: NWConnection) async throws -> (Data?, Bool) {
        try await withCheckedThrowingContinuation { cont in
            c.receive(minimumIncompleteLength: 1, maximumLength: 1 << 20) { d, _, complete, err in
                if let err = err { cont.resume(throwing: err) } else { cont.resume(returning: (d, complete)) }
            }
        }
    }

    private static func send(_ c: NWConnection, _ d: Data) async throws {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            c.send(content: d, completion: .contentProcessed { e in if let e = e { cont.resume(throwing: e) } else { cont.resume() } })
        }
    }

    /// one full reply (handles multi-line "123-" replies)
    private func reply() async throws -> (Int, String) {
        var lines: [String] = []
        while true {
            while let r = buffer.range(of: Data([0x0A])) {
                let line = String(decoding: buffer[buffer.startIndex..<r.lowerBound], as: UTF8.self).trimmingCharacters(in: CharacterSet(charactersIn: "\r"))
                buffer.removeSubrange(buffer.startIndex..<r.upperBound)
                lines.append(line)
                if line.count >= 4, let code = Int(line.prefix(3)), line[line.index(line.startIndex, offsetBy: 3)] == " " {
                    return (code, lines.joined(separator: "\n"))
                }
                if lines.count == 1, line.count == 3, let code = Int(line) { return (code, line) }
            }
            guard let c = ctrl else { throw FTPError.failed("غير متصل") }
            let (d, complete) = try await FTPClient.receive(c)
            if let d = d { buffer.append(d) }
            if complete && (d == nil || d!.isEmpty) { throw FTPError.failed("أغلق الجهاز الاتصال") }
        }
    }

    @discardableResult
    private func cmd(_ s: String, expect: ClosedRange<Int> = 100...399) async throws -> (Int, String) {
        guard let c = ctrl else { throw FTPError.failed("غير متصل") }
        try await FTPClient.send(c, Data((s + "\r\n").utf8))
        let r = try await reply()
        if !expect.contains(r.0) { throw FTPError.failed("\(s.split(separator: " ").first ?? ""): \(r.1)") }
        return r
    }

    func connect() async throws {
        ctrl = try await FTPClient.open(host, port, queue: q)
        let hello = try await reply()
        guard hello.0 < 400 else { throw FTPError.failed(hello.1) }
        let u = try await cmd("USER anonymous", expect: 200...399)
        if u.0 == 331 { try await cmd("PASS arcadia@", expect: 200...299) }
        try await cmd("TYPE I")
    }

    func close() {
        if let c = ctrl { c.send(content: Data("QUIT\r\n".utf8), completion: .idempotent); c.cancel() }
        ctrl = nil
    }

    private func pasv() async throws -> NWConnection {
        let (_, text) = try await cmd("PASV", expect: 227...227)
        guard let open = text.firstIndex(of: "("), let close = text.firstIndex(of: ")") else { throw FTPError.failed("PASV") }
        let nums = text[text.index(after: open)..<close].split(separator: ",").compactMap { Int($0.trimmingCharacters(in: .whitespaces)) }
        guard nums.count == 6 else { throw FTPError.failed("PASV") }
        var h = nums[0...3].map(String.init).joined(separator: ".")
        if h == "0.0.0.0" || h.hasPrefix("127.") { h = host }
        return try await FTPClient.open(h, UInt16(nums[4] * 256 + nums[5]), queue: q)
    }

    private func readAll(_ dc: NWConnection, sink: (Data) throws -> Void) async throws {
        while true {
            let (d, complete) = try await FTPClient.receive(dc)
            if let d = d, !d.isEmpty { try sink(d) }
            if complete { break }
        }
        dc.cancel()
    }

    // ---------- commands
    func list(_ path: String) async throws -> [FTPEntry] {
        // MLSD first, LIST as fallback
        if let r = try? await listMLSD(path) { return r }
        let dc = try await pasv()
        try await cmd("LIST \(path)", expect: 100...199)
        var data = Data()
        try await readAll(dc) { data.append($0) }
        _ = try await reply()
        var out: [FTPEntry] = []
        for line in String(decoding: data, as: UTF8.self).split(whereSeparator: \.isNewline) {
            let p = line.split(separator: " ", maxSplits: 8, omittingEmptySubsequences: true)
            guard p.count >= 9 else { continue }
            let name = String(p[8]); if name == "." || name == ".." { continue }
            out.append(FTPEntry(name: name, isDir: line.hasPrefix("d"), size: Int64(p[4]) ?? 0, date: p[5...7].joined(separator: " ")))
        }
        return sorted(out)
    }

    private func listMLSD(_ path: String) async throws -> [FTPEntry] {
        let dc = try await pasv()
        try await cmd("MLSD \(path)", expect: 100...199)
        var data = Data()
        try await readAll(dc) { data.append($0) }
        _ = try await reply()
        var out: [FTPEntry] = []
        for line in String(decoding: data, as: UTF8.self).split(whereSeparator: \.isNewline) {
            guard let sp = line.firstIndex(of: " ") else { continue }
            let name = String(line[line.index(after: sp)...]); if name == "." || name == ".." { continue }
            var facts: [String: String] = [:]
            for f in line[..<sp].split(separator: ";") {
                let kv = f.split(separator: "=", maxSplits: 1)
                if kv.count == 2 { facts[kv[0].lowercased()] = String(kv[1]) }
            }
            let t = facts["type"] ?? "file"
            if t == "cdir" || t == "pdir" { continue }
            var date = facts["modify"] ?? ""
            if date.count >= 12 {
                let c = Array(date)
                date = "\(String(c[0..<4]))-\(String(c[4..<6]))-\(String(c[6..<8])) \(String(c[8..<10])):\(String(c[10..<12]))"
            }
            out.append(FTPEntry(name: name, isDir: t == "dir", size: Int64(facts["size"] ?? "0") ?? 0, date: date))
        }
        return sorted(out)
    }

    private func sorted(_ a: [FTPEntry]) -> [FTPEntry] {
        a.sorted { ($0.isDir ? 0 : 1, $0.name.lowercased()) < ($1.isDir ? 0 : 1, $1.name.lowercased()) }
    }

    func download(_ path: String, to local: URL, progress: ((Int64, Int64) -> Void)? = nil) async throws {
        let total = (try? await cmd("SIZE \(path)", expect: 200...299)).flatMap { Int64($0.1.split(separator: " ").last ?? "") } ?? 0
        let dc = try await pasv()
        try await cmd("RETR \(path)", expect: 100...199)
        FileManager.default.createFile(atPath: local.path, contents: nil)
        let fh = try FileHandle(forWritingTo: local)
        defer { try? fh.close() }
        var got: Int64 = 0
        try await readAll(dc) { d in
            fh.write(d); got += Int64(d.count); progress?(got, max(total, got))
        }
        _ = try await reply()
    }

    func upload(_ data: Data, to path: String, progress: ((Int64, Int64) -> Void)? = nil) async throws {
        try await upload(stream: InputStream(data: data), size: Int64(data.count), to: path, progress: progress)
    }

    func upload(file: URL, to path: String, progress: ((Int64, Int64) -> Void)? = nil) async throws {
        let size = (try? FileManager.default.attributesOfItem(atPath: file.path)[.size] as? Int64) ?? 0
        guard let s = InputStream(url: file) else { throw FTPError.failed("تعذّر فتح الملف") }
        try await upload(stream: s, size: size, to: path, progress: progress)
    }

    private func upload(stream: InputStream, size: Int64, to path: String, progress: ((Int64, Int64) -> Void)?) async throws {
        let dc = try await pasv()
        try await cmd("STOR \(path)", expect: 100...199)
        stream.open(); defer { stream.close() }
        var buf = [UInt8](repeating: 0, count: 1 << 20)
        var sent: Int64 = 0
        while true {
            let n = stream.read(&buf, maxLength: buf.count)
            if n <= 0 { break }
            try await FTPClient.send(dc, Data(buf[0..<n]))
            sent += Int64(n); progress?(sent, max(size, sent))
        }
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            dc.send(content: nil, contentContext: .finalMessage, isComplete: true, completion: .contentProcessed { _ in cont.resume() })
        }
        dc.cancel()
        let r = try await reply()
        if r.0 >= 400 { throw FTPError.failed(r.1) }
    }

    func mkdirs(_ path: String) async throws {
        var cur = ""
        for part in path.split(separator: "/") {
            cur += "/" + part
            _ = try? await cmd("MKD \(cur)", expect: 200...599)
        }
    }

    func delete(_ path: String, isDir: Bool) async throws {
        if isDir {
            for e in try await list(path) { try await delete(path + "/" + e.name, isDir: e.isDir) }
            try await cmd("RMD \(path)", expect: 200...299)
        } else {
            try await cmd("DELE \(path)", expect: 200...299)
        }
    }

    func rename(_ from: String, _ to: String) async throws {
        try await cmd("RNFR \(from)", expect: 300...399)
        try await cmd("RNTO \(to)", expect: 200...299)
    }
}
