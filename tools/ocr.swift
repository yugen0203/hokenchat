// 画像の文字を読み取る（macOS 標準の Vision。端末内で処理し、外部には送らない）
// ビルド: swiftc -O tools/ocr.swift -o tools/ocr     使い方: tools/ocr 画像ファイル
import AppKit
import Vision

guard CommandLine.arguments.count > 1,
      let img = NSImage(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("画像を開けません\n".data(using: .utf8)!)
    exit(1)
}
let req = VNRecognizeTextRequest()
req.recognitionLanguages = ["ja-JP", "en-US"]
req.recognitionLevel = .accurate
req.usesLanguageCorrection = true
try VNImageRequestHandler(cgImage: cg).perform([req])
// ponytail: 上から順に並べるだけ。段組み（2列）の資料は順番が崩れることがある
for o in (req.results ?? []).sorted(by: { $0.boundingBox.minY > $1.boundingBox.minY }) {
    print(o.topCandidates(1).first?.string ?? "")
}
