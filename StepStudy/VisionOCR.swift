import AppKit
import Foundation
import Vision

struct Result: Codable {
    let text: String
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data(message.utf8))
    exit(2)
}

guard CommandLine.arguments.count == 2 else {
    fail("Expected one image path")
}
let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let image = NSImage(contentsOf: url),
      let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    fail("Image could not be opened")
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.minimumTextHeight = 0.004
let handler = VNImageRequestHandler(cgImage: cgImage)
do {
    try handler.perform([request])
    let lines = (request.results ?? []).compactMap { observation -> (CGFloat, CGFloat, String)? in
        guard let text = observation.topCandidates(1).first?.string else { return nil }
        return (observation.boundingBox.midY, observation.boundingBox.minX, text)
    }.sorted { lhs, rhs in
        if abs(lhs.0 - rhs.0) > 0.012 { return lhs.0 > rhs.0 }
        return lhs.1 < rhs.1
    }.map { $0.2 }
    let data = try JSONEncoder().encode(Result(text: lines.joined(separator: "\n")))
    FileHandle.standardOutput.write(data)
} catch {
    fail("Vision request failed: \(error.localizedDescription)")
}
