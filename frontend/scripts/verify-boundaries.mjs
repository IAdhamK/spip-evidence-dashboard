import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const featuresRoot = join(root, "src", "features");
const mainPath = join(root, "src", "main.jsx");
const errors = [];

function source(path) {
  return readFileSync(path, "utf8");
}

function featureFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? featureFiles(path) : /\.[cm]?[jt]sx?$/.test(entry.name) ? [path] : [];
  });
}

const main = source(mainPath);
if (!main.includes('from "./features/GuidedReviewPage.jsx"')) {
  errors.push("main.jsx harus memakai feature module GuidedReviewPage.");
}
if (!main.includes('from "./features/VisualReviewPage.jsx"')) {
  errors.push("main.jsx harus memakai feature module VisualReviewPage.");
}
if (!main.includes('from "./features/GovernancePage.jsx"')) {
  errors.push("main.jsx harus memakai feature module GovernancePage.");
}
if (!main.includes('from "./features/SmartUploadPage.jsx"')) {
  errors.push("main.jsx harus memakai feature module SmartUploadPage.");
}
if (/function\s+OperationalReadinessPanel\s*\(/.test(main)) {
  errors.push("OperationalReadinessPanel tidak boleh didefinisikan kembali di main.jsx.");
}
if (main.includes('className="release-governance-workspace"')) {
  errors.push("Workspace Bukti Rilis tidak boleh didefinisikan kembali di main.jsx.");
}
if (/function\s+GuidedReviewPage\s*\(/.test(main)) {
  errors.push("GuidedReviewPage tidak boleh didefinisikan kembali di main.jsx.");
}
if (/function\s+VisualReviewPage\s*\(/.test(main)) {
  errors.push("VisualReviewPage tidak boleh didefinisikan kembali di main.jsx.");
}
if (/function\s+GovernancePage\s*\(/.test(main)) {
  errors.push("GovernancePage tidak boleh didefinisikan kembali di main.jsx.");
}
if (/function\s+SmartUploadPage\s*\(/.test(main)) {
  errors.push("SmartUploadPage tidak boleh didefinisikan kembali di main.jsx.");
}

for (const path of featureFiles(featuresRoot)) {
  const content = source(path);
  const name = relative(root, path);
  if (/from\s+["'][^"']*main\.jsx["']/.test(content)) {
    errors.push(`${name} tidak boleh mengimpor composition root main.jsx.`);
  }
}

for (const presentational of [
  "OperationalReadinessPanel.jsx",
  "ReleaseGovernanceWorkspace.jsx",
  "ShadowComparisonCard.jsx",
  "shared/Feedback.jsx",
  "shared/StatusPill.jsx",
]) {
  const content = source(join(featuresRoot, presentational));
  for (const forbidden of ["apiGet(", "apiPost(", "apiUpload(", "useEffect(", "useState("]) {
    if (content.includes(forbidden)) {
      errors.push(`${presentational} harus presentational; ditemukan ${forbidden}.`);
    }
  }
}

const guided = source(join(featuresRoot, "GuidedReviewPage.jsx"));
for (const forbidden of ["apiUpload(", "window.fetch(", "fetch("]) {
  if (guided.includes(forbidden)) {
    errors.push(`GuidedReviewPage tidak boleh memakai ${forbidden}.`);
  }
}
const guidedApiLiterals = guided.match(/["'`]\/api\/[^"'`]+/g) ?? [];
for (const literal of guidedApiLiterals) {
  const endpoint = literal.slice(1);
  if (!endpoint.startsWith("/api/analysis-runs/guided-review/")) {
    errors.push(`GuidedReviewPage keluar dari endpoint ownership: ${endpoint}`);
  }
}
if (!guidedApiLiterals.length) {
  errors.push("GuidedReviewPage tidak mempunyai endpoint guided-review yang dapat diverifikasi.");
}

const visual = source(join(featuresRoot, "VisualReviewPage.jsx"));
for (const forbidden of ["apiUpload(", "window.fetch(", "fetch("]) {
  if (visual.includes(forbidden)) {
    errors.push(`VisualReviewPage tidak boleh memakai ${forbidden}.`);
  }
}
const visualApiLiterals = visual.match(/["'`]\/api\/[^"'`]+/g) ?? [];
for (const literal of visualApiLiterals) {
  const endpoint = literal.slice(1);
  if (!endpoint.startsWith("/api/analysis-runs/visual-review/")) {
    errors.push(`VisualReviewPage keluar dari endpoint ownership: ${endpoint}`);
  }
}
if (!visualApiLiterals.length) {
  errors.push("VisualReviewPage tidak mempunyai endpoint visual-review yang dapat diverifikasi.");
}

const governance = source(join(featuresRoot, "GovernancePage.jsx"));
for (const forbidden of ["apiUpload(", "window.fetch(", "fetch("]) {
  if (governance.includes(forbidden)) {
    errors.push(`GovernancePage tidak boleh memakai ${forbidden}.`);
  }
}
const governanceApiPrefixes = [
  "/api/analysis-runs/governance/",
  "/api/analysis-runs/release-evidence",
  "/api/analysis-runs/evaluation-reports/",
  "/api/analysis-runs/shadow-comparisons/",
  "/api/analysis-runs/guided-review/",
];
const governanceApiLiterals = governance.match(/["'`]\/api\/[^"'`]+/g) ?? [];
for (const literal of governanceApiLiterals) {
  const endpoint = literal.slice(1);
  if (!governanceApiPrefixes.some((prefix) => endpoint.startsWith(prefix))) {
    errors.push(`GovernancePage keluar dari endpoint ownership: ${endpoint}`);
  }
}
if (!governanceApiLiterals.length) {
  errors.push("GovernancePage tidak mempunyai endpoint governance yang dapat diverifikasi.");
}

const smartUpload = source(join(featuresRoot, "SmartUploadPage.jsx"));
if (!smartUpload.includes('from "./OperationalReadinessPanel.jsx"')) {
  errors.push("SmartUploadPage harus merender OperationalReadinessPanel feature module.");
}
const smartUploadModulePaths = [
  "smart-upload/BatchZipIntakePanel.jsx",
  "smart-upload/SmartUploadControls.jsx",
  "smart-upload/SmartUploadLinkPanel.jsx",
  "smart-upload/DocumentIntelligenceResult.jsx",
  "smart-upload/ControlledUploadState.jsx",
  "smart-upload/SmartUploadBatchPanels.jsx",
  "smart-upload/SmartUploadResults.jsx",
  "smart-upload/utils.js",
];
for (const modulePath of smartUploadModulePaths) {
  const importName = modulePath.split("/").at(-1);
  if (!smartUpload.includes(`./smart-upload/${importName}`) && !smartUploadModulePaths.some((candidate) => {
    const candidateSource = source(join(featuresRoot, candidate));
    return candidateSource.includes(`./${importName}`);
  })) {
    errors.push(`${modulePath} harus terhubung dari SmartUploadPage atau submodule Smart Upload.`);
  }
}
for (const movedComponent of [
  "BatchZipIntakePanel",
  "SmartUploadProgressBoard",
  "AnalysisModePicker",
  "CandidateLimitControl",
  "LinkCrawlPanel",
  "SmartUploadResults",
  "DocumentIntelligenceResult",
]) {
  if (new RegExp(`function\\s+${movedComponent}\\s*\\(`).test(smartUpload)) {
    errors.push(`${movedComponent} tidak boleh didefinisikan kembali di SmartUploadPage root.`);
  }
}

const smartUploadSources = [
  ["SmartUploadPage.jsx", smartUpload],
  ...smartUploadModulePaths.map((path) => [path, source(join(featuresRoot, path))]),
];
for (const [name, content] of smartUploadSources) {
  for (const forbidden of ["window.fetch(", "fetch("]) {
    if (content.includes(forbidden)) {
      errors.push(`${name} tidak boleh memakai ${forbidden}; gunakan API helper.`);
    }
  }
}
const smartUploadApiPrefixes = [
  "/api/smart-upload/",
  "/api/analysis-runs",
  "/api/analysis-packages",
];
let smartUploadApiLiteralCount = 0;
for (const [name, content] of smartUploadSources) {
  const literals = content.match(/["'`]\/api\/[^"'`]+/g) ?? [];
  smartUploadApiLiteralCount += literals.length;
  for (const literal of literals) {
    const endpoint = literal.slice(1);
    if (!smartUploadApiPrefixes.some((prefix) => endpoint.startsWith(prefix))) {
      errors.push(`${name} keluar dari endpoint ownership: ${endpoint}`);
    }
  }
}
if (!smartUploadApiLiteralCount) {
  errors.push("Feature Smart Upload tidak mempunyai endpoint upload yang dapat diverifikasi.");
}
const intelligenceResult = source(join(featuresRoot, "smart-upload/DocumentIntelligenceResult.jsx"));
const controlledUploadState = source(join(featuresRoot, "smart-upload/ControlledUploadState.jsx"));
const controlledUploadContract = `${intelligenceResult}\n${controlledUploadState}`;
for (const required of ["/approve-upload", "controlled_upload_actions", "blocked_ambiguous", "/reconciliation", "expected_latest_event_id"]) {
  if (!controlledUploadContract.includes(required)) {
    errors.push(`DocumentIntelligenceResult harus mempertahankan controlled-upload safety contract: ${required}.`);
  }
}
if (intelligenceResult.includes("${run.id}/controlled-upload`")) {
  errors.push("UI V2 harus memakai endpoint roadmap approve-upload; controlled-upload hanya alias backend.");
}

for (const presentational of [
  "smart-upload/BatchZipIntakePanel.jsx",
  "smart-upload/SmartUploadControls.jsx",
  "smart-upload/SmartUploadLinkPanel.jsx",
  "smart-upload/SmartUploadBatchPanels.jsx",
  "smart-upload/ControlledUploadState.jsx",
  "smart-upload/utils.js",
]) {
  const content = source(join(featuresRoot, presentational));
  for (const forbidden of ["apiGet(", "apiPost(", "apiUpload(", "useEffect(", "useState(", "useMemo("]) {
    if (content.includes(forbidden)) {
      errors.push(`${presentational} harus presentational; ditemukan ${forbidden}.`);
    }
  }
}

for (const [path, maximumLines] of [
  ["SmartUploadPage.jsx", 650],
  ["smart-upload/DocumentIntelligenceResult.jsx", 400],
  ["smart-upload/SmartUploadResults.jsx", 550],
]) {
  const lineCount = source(join(featuresRoot, path)).split(/\r?\n/).length;
  if (lineCount > maximumLines) {
    errors.push(`${path} memiliki ${lineCount} baris; batas reviewability ${maximumLines}.`);
  }
}

if (errors.length) {
  for (const error of errors) process.stderr.write(`- ${error}\n`);
  process.exit(1);
}

process.stdout.write("Frontend V2 module boundaries valid.\n");
