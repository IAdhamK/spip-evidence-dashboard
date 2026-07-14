import Foundation
import Vision

struct Region: Codable {
    let text: String
    let confidence: Double
    let bbox: [String: Double]
    let coordinate_space: String
}

struct Output: Codable {
    let text: String
    let confidence: Double
    let regions: [Region]
    let languages: [String]
}

guard CommandLine.arguments.count == 2 else {
    FileHandle.standardError.write(Data("usage: local_ocr_vision <image>\n".utf8))
    exit(2)
}

let imageURL = URL(fileURLWithPath: CommandLine.arguments[1])

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
let supported = try request.supportedRecognitionLanguages()
let preferred = ["id-ID", "en-US"].filter { supported.contains($0) }
if !preferred.isEmpty {
    request.recognitionLanguages = preferred
}

let handler = VNImageRequestHandler(url: imageURL, options: [:])
do {
    try handler.perform([request])
} catch {
    FileHandle.standardError.write(Data("vision request failed: \(error)\n".utf8))
    exit(4)
}

let observations = request.results ?? []
var regions: [Region] = []
var weightedConfidence = 0.0
var totalWeight = 0.0
for observation in observations {
    guard let candidate = observation.topCandidates(1).first else { continue }
    let value = candidate.string.trimmingCharacters(in: .whitespacesAndNewlines)
    if value.isEmpty { continue }
    let box = observation.boundingBox
    let topY = 1.0 - Double(box.origin.y) - Double(box.size.height)
    let weight = Double(max(1, value.count))
    let confidence = Double(candidate.confidence)
    weightedConfidence += confidence * weight
    totalWeight += weight
    regions.append(Region(
        text: value,
        confidence: confidence,
        bbox: [
            "x": Double(box.origin.x),
            "y": max(0.0, topY),
            "width": Double(box.size.width),
            "height": Double(box.size.height),
        ],
        coordinate_space: "normalized_top_left"
    ))
}

let output = Output(
    text: regions.map { $0.text }.joined(separator: "\n"),
    confidence: totalWeight > 0 ? weightedConfidence / totalWeight : 0.0,
    regions: regions,
    languages: request.recognitionLanguages
)
let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys]
let data = try encoder.encode(output)
FileHandle.standardOutput.write(data)
