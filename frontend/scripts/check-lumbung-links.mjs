import assert from "node:assert/strict";

import {
  SPECIAL_KK32_310_PARAMETER,
  SPECIAL_KK32_310_ROOT,
  SPECIAL_KK32_310_SUBUNSUR,
  canonicalFolderPath,
  canonicalLumbungUrl,
  normalizeLumbungLinks,
} from "../src/lib/lumbung-link.js";

const fullPath = [
  "KK 3.3 PENGAMANAN ASET NEGARA DAERAH",
  "1.5 Pendelegasian Wewenang dan Tanggung Jawab yang Tepat",
  "1.5.1 Wewenang dan tanggung jawab pengelolaan aset diberikan kepada pegawai yang tepat sesuai tingkatannya untuk mendukung efektivitas dan efisiensi pelaksanaan kegiatan dan memperhatikan benturan kepentingan",
  "Grade C",
].join("/");
const malformedUrl = `https://lumbungfile.kemendesa.go.id/s/CiJYTHFxZaJ83YF?dir=/${fullPath.split("/").map(encodeURIComponent).join("/")}`;
const canonicalPath = canonicalFolderPath(fullPath);
const parameterSegment = canonicalPath.split("/")[2];
const evaluasiPath = [
  "KK 3.3 PENGAMANAN ASET NEGARA DAERAH",
  "5.2 Evaluasi Terpisah",
  "5.2.1 Evaluasi terpisah dilakukan oleh pegawai dengan keahlian tertentu yang disyaratkan dan dapat melibatkan APIP atau auditor eksternal untuk menilai kinerja sistem pengendalian intern, mengidentifikasi kelemahan pengendalian, menentukan",
  "Grade A",
].join("/");
const malformedEvaluasiUrl = `https://lumbungfile.kemendesa.go.id/s/CiJYTHFxZaJ83YF?dir=/${evaluasiPath.split("/").map(encodeURIComponent).join("/")}`;
const canonicalEvaluasiPath = canonicalFolderPath(evaluasiPath);
const evaluasiParameterSegment = canonicalEvaluasiPath.split("/")[2];

assert.equal(Array.from(parameterSegment).length, 118);
assert.ok(parameterSegment.endsWith("mend_"));
assert.equal(
  canonicalLumbungUrl(malformedUrl),
  `https://lumbungfile.kemendesa.go.id/s/CiJYTHFxZaJ83YF?dir=/${canonicalPath.split("/").map(encodeURIComponent).join("/")}`,
);
assert.equal(
  canonicalLumbungUrl(malformedUrl, canonicalPath),
  canonicalLumbungUrl(malformedUrl),
);

const normalizedPayload = normalizeLumbungLinks({
  dashboard: {
    folders: [
      {
        folder_path: fullPath,
        public_url: malformedUrl,
      },
    ],
  },
});
assert.equal(normalizedPayload.dashboard.folders[0].folder_path, canonicalPath);
assert.equal(normalizedPayload.dashboard.folders[0].public_url, canonicalLumbungUrl(malformedUrl));
assert.equal(Array.from(evaluasiParameterSegment).length, 118);
assert.ok(evaluasiParameterSegment.endsWith("APIP at_"));
assert.equal(
  canonicalLumbungUrl(malformedEvaluasiUrl),
  `https://lumbungfile.kemendesa.go.id/s/CiJYTHFxZaJ83YF?dir=/${canonicalEvaluasiPath.split("/").map(encodeURIComponent).join("/")}`,
);

const staleSpecialParameter = `${Array.from(SPECIAL_KK32_310_PARAMETER).slice(0, 117).join("")}_`;
for (const grade of ["A", "B", "C", "D", "E"]) {
  const staleSpecialPath = [
    SPECIAL_KK32_310_ROOT,
    SPECIAL_KK32_310_SUBUNSUR,
    staleSpecialParameter,
    `Grade ${grade}`,
  ].join("/");
  const expectedSpecialPath = [
    SPECIAL_KK32_310_ROOT,
    SPECIAL_KK32_310_SUBUNSUR,
    SPECIAL_KK32_310_PARAMETER,
    `Grade ${grade}`,
  ].join("/");
  const staleSpecialUrl = `https://lumbungfile.kemendesa.go.id/s/CiJYTHFxZaJ83YF?dir=/${staleSpecialPath.split("/").map(encodeURIComponent).join("/")}`;

  assert.ok(Array.from(SPECIAL_KK32_310_PARAMETER).length > 118);
  assert.equal(canonicalFolderPath(staleSpecialPath), expectedSpecialPath);
  assert.equal(
    canonicalLumbungUrl(staleSpecialUrl),
    `https://lumbungfile.kemendesa.go.id/s/CiJYTHFxZaJ83YF?dir=/${expectedSpecialPath.split("/").map(encodeURIComponent).join("/")}`,
  );
}

console.log("LumbungFile link checks passed.");
