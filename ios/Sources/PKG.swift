import Foundation
import CommonCrypto
import CryptoKit

struct PkgEntry: Identifiable, Hashable {
    var id: String { name }
    let name: String, offset: UInt64, size: UInt64, flags: UInt32
    var isDir: Bool { flags & 0xFF == 4 }
}

/// PS3/PSP package reader (debug and retail), like the Windows Studio's PkgReader.
final class PkgReader {
    let url: URL
    private let fh: FileHandle
    let header: Data
    let retail: Bool, pkgType: UInt16
    let count: UInt32, dataOff: UInt64, totalSize: UInt64
    let contentID: String
    private let qa: Data, riv: Data, key: Data
    static let ps3Key = Data([0x2E, 0x7B, 0x71, 0xD7, 0xC9, 0xC9, 0xA1, 0x4E, 0xA3, 0x22, 0x1F, 0x18, 0x88, 0x28, 0xB8, 0xF8])
    static let pspKey = Data([0x07, 0xF2, 0xC6, 0x82, 0x90, 0xB5, 0x0D, 0x2C, 0x33, 0x81, 0x8D, 0x70, 0x9B, 0x60, 0xE6, 0x2B])

    init(_ url: URL) throws {
        self.url = url
        fh = try FileHandle(forReadingFrom: url)
        header = fh.readData(ofLength: 0x180)
        guard header.count >= 0x80, header.prefix(4) == Data([0x7F, 0x50, 0x4B, 0x47]) else { throw FTPError.failed("ليس ملف PKG صالحاً") }
        let rev = header.be16(4); pkgType = header.be16(6)
        retail = rev & 0x8000 != 0
        if retail && pkgType != 1 && pkgType != 2 { throw FTPError.failed("نوع حزمة غير مدعوم (PS Vita/PSM)") }
        count = header.be32(0x14); totalSize = header.be64(0x18); dataOff = header.be64(0x20)
        contentID = String(decoding: header[0x30..<0x60].prefix { $0 != 0 }, as: UTF8.self)
        qa = header.subdata(in: 0x60..<0x70); riv = header.subdata(in: 0x70..<0x80)
        key = pkgType == 1 ? PkgReader.ps3Key : PkgReader.pspKey
    }
    deinit { try? fh.close() }

    var kind: String { (retail ? "Retail" : "Debug") + (pkgType == 1 ? " PS3" : " PSP") }

    // debug packages: SHA-1 keystream
    private func debugCrypt(_ data: Data, block: UInt64) -> Data {
        var k = [UInt8](repeating: 0, count: 0x40)
        let q = [UInt8](qa)
        for i in 0..<8 { k[i] = q[i]; k[8 + i] = q[i]; k[16 + i] = q[8 + i]; k[24 + i] = q[8 + i] }
        var out = [UInt8](data)
        var b = block
        var pos = 0
        while pos < out.count {
            for i in 0..<8 { k[0x38 + i] = UInt8((b >> (56 - 8 * UInt64(i))) & 0xFF) }
            let h = Array(Insecure.SHA1.hash(data: k))
            for i in 0..<min(16, out.count - pos) { out[pos + i] ^= h[i] }
            pos += 16; b &+= 1
        }
        return Data(out)
    }

    // retail packages: AES-128-CTR with the public PS3/PSP key
    private func retailCrypt(_ data: Data, block: UInt64) -> Data {
        var iv = [UInt8](riv)
        var carry = block
        for i in stride(from: 15, through: 0, by: -1) where carry > 0 {
            let s = UInt64(iv[i]) + (carry & 0xFF)
            iv[i] = UInt8(s & 0xFF); carry = (carry >> 8) + (s >> 8)
        }
        var out = [UInt8](repeating: 0, count: data.count)
        var cryptor: CCCryptorRef?
        _ = key.withUnsafeBytes { kp in
            CCCryptorCreateWithMode(CCOperation(kCCEncrypt), CCMode(kCCModeCTR), CCAlgorithm(kCCAlgorithmAES), CCPadding(ccNoPadding),
                                    iv, kp.baseAddress, key.count, nil, 0, 0, CCModeOptions(kCCModeOptionCTR_BE), &cryptor)
        }
        var moved = 0
        _ = data.withUnsafeBytes { dp in CCCryptorUpdate(cryptor, dp.baseAddress, data.count, &out, out.count, &moved) }
        CCCryptorRelease(cryptor)
        return Data(out)
    }

    func read(_ off: UInt64, _ size: Int) -> Data {
        let start = (off / 16) * 16, pre = Int(off % 16)
        try? fh.seek(toOffset: dataOff + start)
        let blob = fh.readData(ofLength: ((pre + size + 15) / 16) * 16)
        let dec = retail ? retailCrypt(blob, block: off / 16) : debugCrypt(blob, block: off / 16)
        guard dec.count >= pre else { return Data() }
        return dec.subdata(in: pre..<min(dec.count, pre + size))
    }

    lazy var entries: [PkgEntry] = {
        let tbl = read(0, Int(count) * 32)
        var out: [PkgEntry] = []
        for i in 0..<Int(count) {
            let b = i * 32
            guard tbl.count >= b + 32 else { break }
            let no = UInt64(tbl.be32(b)), ns = Int(tbl.be32(b + 4)), dOff = tbl.be64(b + 8), dSize = tbl.be64(b + 16), fl = tbl.be32(b + 24)
            let name = String(decoding: read(no, ns), as: UTF8.self)
            out.append(PkgEntry(name: name, offset: dOff, size: dSize, flags: fl))
        }
        return out
    }()

    func readFile(_ name: String) -> Data? {
        guard let e = entries.first(where: { $0.name == name && !$0.isDir }) else { return nil }
        return read(e.offset, Int(e.size))
    }

    func extract(_ e: PkgEntry, to dir: URL, progress: ((Int64) -> Void)? = nil) throws -> URL {
        let dst = dir.appendingPathComponent(e.name)
        if e.isDir { try FileManager.default.createDirectory(at: dst, withIntermediateDirectories: true); return dst }
        try FileManager.default.createDirectory(at: dst.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: dst.path, contents: nil)
        let out = try FileHandle(forWritingTo: dst); defer { try? out.close() }
        var pos: UInt64 = 0
        while pos < e.size {
            let n = Int(min(UInt64(8 << 20), e.size - pos))
            out.write(read(e.offset + pos, n)); pos += UInt64(n); progress?(Int64(n))
        }
        return dst
    }
}

enum SFO {
    static func parse(_ d: Data?) -> [String: String] {
        guard let d = d, d.count > 20, d.prefix(4) == Data([0, 0x50, 0x53, 0x46]) else { return [:] }
        let kt = Int(d.le32(8)), dt = Int(d.le32(12)), n = Int(d.le32(16))
        var out: [String: String] = [:]
        for i in 0..<n {
            let e = 20 + i * 16
            guard d.count >= e + 16 else { break }
            let ko = Int(d.le16(e)), fmt = d.le16(e + 2), ln = Int(d.le32(e + 4)), dOff = Int(d.le32(e + 12))
            var kEnd = kt + ko
            while kEnd < d.count && d[kEnd] != 0 { kEnd += 1 }
            let key = String(decoding: d[(kt + ko)..<kEnd], as: UTF8.self)
            guard dt + dOff + ln <= d.count else { continue }
            let raw = d.subdata(in: (dt + dOff)..<(dt + dOff + ln))
            if fmt == 0x0404 { out[key] = String(raw.le32(0)) }
            else { out[key] = String(decoding: raw.prefix { $0 != 0 }, as: UTF8.self) }
        }
        return out
    }
}

extension Data {
    func be16(_ o: Int) -> UInt16 { UInt16(self[startIndex + o]) << 8 | UInt16(self[startIndex + o + 1]) }
    func be32(_ o: Int) -> UInt32 { (0..<4).reduce(0) { $0 << 8 | UInt32(self[startIndex + o + $1]) } }
    func be64(_ o: Int) -> UInt64 { (0..<8).reduce(0) { $0 << 8 | UInt64(self[startIndex + o + $1]) } }
    func le16(_ o: Int) -> UInt16 { UInt16(self[startIndex + o]) | UInt16(self[startIndex + o + 1]) << 8 }
    func le32(_ o: Int) -> UInt32 { (0..<4).reversed().reduce(0) { $0 << 8 | UInt32(self[startIndex + o + $1]) } }
}
