import Foundation

/// The PS3 XMB has no Arabic shaping and no right-to-left support, so the store keeps Arabic text
/// already joined (presentation forms) and in visual order. Same rules as the Windows Studio.
enum Arabic {
    // isolated, final, initial, medial (0 = form does not exist)
    private static let forms: [UInt32: [UInt32]] = [
        0x0621: [0xFE80, 0, 0, 0],
        0x0622: [0xFE81, 0xFE82, 0, 0], 0x0623: [0xFE83, 0xFE84, 0, 0], 0x0624: [0xFE85, 0xFE86, 0, 0],
        0x0625: [0xFE87, 0xFE88, 0, 0], 0x0626: [0xFE89, 0xFE8A, 0xFE8B, 0xFE8C], 0x0627: [0xFE8D, 0xFE8E, 0, 0],
        0x0628: [0xFE8F, 0xFE90, 0xFE91, 0xFE92], 0x0629: [0xFE93, 0xFE94, 0, 0],
        0x062A: [0xFE95, 0xFE96, 0xFE97, 0xFE98], 0x062B: [0xFE99, 0xFE9A, 0xFE9B, 0xFE9C],
        0x062C: [0xFE9D, 0xFE9E, 0xFE9F, 0xFEA0], 0x062D: [0xFEA1, 0xFEA2, 0xFEA3, 0xFEA4],
        0x062E: [0xFEA5, 0xFEA6, 0xFEA7, 0xFEA8], 0x062F: [0xFEA9, 0xFEAA, 0, 0], 0x0630: [0xFEAB, 0xFEAC, 0, 0],
        0x0631: [0xFEAD, 0xFEAE, 0, 0], 0x0632: [0xFEAF, 0xFEB0, 0, 0],
        0x0633: [0xFEB1, 0xFEB2, 0xFEB3, 0xFEB4], 0x0634: [0xFEB5, 0xFEB6, 0xFEB7, 0xFEB8],
        0x0635: [0xFEB9, 0xFEBA, 0xFEBB, 0xFEBC], 0x0636: [0xFEBD, 0xFEBE, 0xFEBF, 0xFEC0],
        0x0637: [0xFEC1, 0xFEC2, 0xFEC3, 0xFEC4], 0x0638: [0xFEC5, 0xFEC6, 0xFEC7, 0xFEC8],
        0x0639: [0xFEC9, 0xFECA, 0xFECB, 0xFECC], 0x063A: [0xFECD, 0xFECE, 0xFECF, 0xFED0],
        0x0640: [0x0640, 0x0640, 0x0640, 0x0640],
        0x0641: [0xFED1, 0xFED2, 0xFED3, 0xFED4], 0x0642: [0xFED5, 0xFED6, 0xFED7, 0xFED8],
        0x0643: [0xFED9, 0xFEDA, 0xFEDB, 0xFEDC], 0x0644: [0xFEDD, 0xFEDE, 0xFEDF, 0xFEE0],
        0x0645: [0xFEE1, 0xFEE2, 0xFEE3, 0xFEE4], 0x0646: [0xFEE5, 0xFEE6, 0xFEE7, 0xFEE8],
        0x0647: [0xFEE9, 0xFEEA, 0xFEEB, 0xFEEC], 0x0648: [0xFEED, 0xFEEE, 0, 0],
        0x0649: [0xFEEF, 0xFEF0, 0, 0], 0x064A: [0xFEF1, 0xFEF2, 0xFEF3, 0xFEF4],
        0x067E: [0xFB56, 0xFB57, 0xFB58, 0xFB59], 0x0686: [0xFB7A, 0xFB7B, 0xFB7C, 0xFB7D],
        0x0698: [0xFB8A, 0xFB8B, 0, 0], 0x06A9: [0xFB8E, 0xFB8F, 0xFB90, 0xFB91],
        0x06AF: [0xFB92, 0xFB93, 0xFB94, 0xFB95], 0x06CC: [0xFBFC, 0xFBFD, 0xFBFE, 0xFBFF],
    ]
    // lam + alef variants -> (isolated, final)
    private static let lamAlef: [UInt32: (UInt32, UInt32)] = [
        0x0622: (0xFEF5, 0xFEF6), 0x0623: (0xFEF7, 0xFEF8), 0x0625: (0xFEF9, 0xFEFA), 0x0627: (0xFEFB, 0xFEFC),
    ]

    static func isHaraka(_ v: UInt32) -> Bool { (0x064B...0x065F).contains(v) || v == 0x0670 }
    static func isArabic(_ v: UInt32) -> Bool {
        (0x0600...0x06FF).contains(v) || (0xFB50...0xFDFF).contains(v) || (0xFE70...0xFEFF).contains(v)
    }
    static func hasArabic(_ s: String) -> Bool { s.unicodeScalars.contains { isArabic($0.value) } }

    private static func dual(_ v: UInt32) -> Bool { (forms[v]?[2] ?? 0) != 0 }

    /// letters -> joined presentation forms (logical order kept)
    static func reshape(_ text: String) -> String {
        let cs = text.unicodeScalars.map { $0.value }.filter { !isHaraka($0) }
        var out: [UInt32] = []
        var i = 0
        func prevJoins(_ idx: Int) -> Bool {
            guard idx > 0 else { return false }
            return dual(cs[idx - 1])
        }
        while i < cs.count {
            let c = cs[i]
            guard let f = forms[c] else { out.append(c); i += 1; continue }
            // lam-alef ligature
            if c == 0x0644, i + 1 < cs.count, let la = lamAlef[cs[i + 1]] {
                out.append(prevJoins(i) ? la.1 : la.0); i += 2; continue
            }
            let p = prevJoins(i)
            let n = i + 1 < cs.count && forms[cs[i + 1]] != nil && dual(c)
            var v: UInt32
            if p && n { v = f[3] } else if p { v = f[1] } else if n { v = f[2] } else { v = f[0] }
            if v == 0 { v = f[0] }
            out.append(v); i += 1
        }
        return String(String.UnicodeScalarView(out.compactMap { Unicode.Scalar($0) }))
    }

    private enum Dir { case l, r, en, n }
    private static func dir(_ v: UInt32) -> Dir {
        if isArabic(v) { return (0x0660...0x0669).contains(v) ? .en : .r }
        if (0x30...0x39).contains(v) { return .en }
        if let s = Unicode.Scalar(v), s.properties.isAlphabetic { return .l }
        return .n
    }
    private static let mirror: [UInt32: UInt32] = [0x28: 0x29, 0x29: 0x28, 0x5B: 0x5D, 0x5D: 0x5B, 0x7B: 0x7D, 0x7D: 0x7B, 0x3C: 0x3E, 0x3E: 0x3C]

    /// logical -> visual order for a right-to-left paragraph (simplified bidi, like python-bidi base_dir='R')
    static func visual(_ text: String) -> String {
        let cs = text.unicodeScalars.map { $0.value }
        if cs.isEmpty { return text }
        var ds = cs.map(dir)
        // rules W4/W5: "3.78", "1,41", "+8%" stay one number
        let sep: Set<UInt32> = [0x2E, 0x2C, 0x3A, 0x2F, 0x2B, 0x2D]
        let term: Set<UInt32> = [0x25, 0x23, 0x24, 0xB0, 0x2030, 0x066A]
        if ds.count > 2 {
            for i in 1..<(ds.count - 1) where ds[i] == .n && sep.contains(cs[i]) && ds[i - 1] == .en && ds[i + 1] == .en { ds[i] = .en }
        }
        var t = 0
        while t < ds.count {
            if ds[t] == .n && term.contains(cs[t]) {
                var j = t
                while j < ds.count && ds[j] == .n && term.contains(cs[j]) { j += 1 }
                if (t > 0 && ds[t - 1] == .en) || (j < ds.count && ds[j] == .en) { for q in t..<j { ds[q] = .en } }
                t = j
            } else { t += 1 }
        }
        // rule W7: numbers following Latin text (no Arabic in between) belong to the Latin run
        var lastStrong: Dir = .r
        for i in 0..<ds.count {
            if ds[i] == .l || ds[i] == .r { lastStrong = ds[i] } else if ds[i] == .en && lastStrong == .l { ds[i] = .l }
        }
        var lv = [Int](repeating: 1, count: cs.count)
        for i in 0..<cs.count where ds[i] == .l || ds[i] == .en { lv[i] = 2 }
        // neutrals: between two strong of the same side (numbers count as R side, rule N1) take that side
        var i = 0
        while i < cs.count {
            if ds[i] != .n { i += 1; continue }
            var j = i
            while j < cs.count && ds[j] == .n { j += 1 }
            let before: Dir = i > 0 ? (ds[i - 1] == .en ? .r : ds[i - 1]) : .r
            let after: Dir = j < cs.count ? (ds[j] == .en ? .r : ds[j]) : .r
            let level = (before == .l && after == .l) ? 2 : 1
            for k in i..<j { lv[k] = level }
            i = j
        }
        // numbers after Latin letters with only spaces between stay in the Latin run (e.g. "GTA 5" -> level 2 already)
        var idx = Array(0..<cs.count)
        for level in stride(from: 2, through: 1, by: -1) {
            var a = 0
            while a < idx.count {
                if lv[idx[a]] >= level {
                    var b = a
                    while b < idx.count && lv[idx[b]] >= level { b += 1 }
                    idx[a..<b].reverse(); a = b
                } else { a += 1 }
            }
        }
        let out: [UInt32] = idx.map { k in
            let v = cs[k]
            return lv[k] % 2 == 1 ? (mirror[v] ?? v) : v
        }
        return String(String.UnicodeScalarView(out.compactMap { Unicode.Scalar($0) }))
    }

    /// normal text -> string stored for the XMB
    static func toXMB(_ text: String) -> String {
        hasArabic(text) ? visual(reshape(text)) : text
    }

    /// XMB string -> editable text (used when the project's sidecar map has no entry)
    static func fromXMB(_ s: String) -> String {
        guard s.unicodeScalars.contains(where: { (0xFB50...0xFEFF).contains($0.value) }) else { return s }
        var out = ""
        for sc in s.unicodeScalars {
            let v = sc.value
            if (0xFEF5...0xFEFC).contains(v) {
                // lam-alef stored in visual order: alef then lam
                let base = String(sc).precomposedStringWithCompatibilityMapping
                out += String(base.reversed())
            } else if (0xFB50...0xFEFF).contains(v) {
                out += String(sc).precomposedStringWithCompatibilityMapping
            } else {
                out.unicodeScalars.append(sc)
            }
        }
        return visual(out)
    }
}
