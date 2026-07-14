import assert from "node:assert/strict";

import { canonicalFolderPath, canonicalLumbungUrl } from "../src/lib/lumbung-link.js";

const fullPath = [
  "KK 3.3 PENGAMANAN ASET NEGARA DAERAH",
  "1.5 Pendelegasian Wewenang dan Tanggung Jawab yang Tepat",
  "1.5.1 Wewenang dan tanggung jawab pengelolaan aset diberikan kepada pegawai yang tepat sesuai tingkatannya untuk mendukung efektivitas dan efisiensi pelaksanaan kegiatan dan memperhatikan benturan kepentingan",
  "Grade C",
].join("/");
const malformedUrl = `https://lumbungfile.kemendesa.go.id/s/CiJYTHFxZaJ83YF?dir=/${fullPath.split("/").map(encodeURIComponent).join("/")}`;
const canonicalPath = canonicalFolderPath(fullPath);
const parameterSegment = canonicalPath.split("/")[2];

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

console.log("LumbungFile link checks passed.");
