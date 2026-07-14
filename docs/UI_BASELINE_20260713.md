# UI Baseline Document Intelligence V2

Tanggal capture: 13 Juli 2026
Runtime: build Vite production lokal pada `127.0.0.1:8091`
Database: fixture sementara hasil migration V1–V19
Viewport: 1280×720
Real upload: nonaktif
Console error: 0

## Build fingerprint

| Artefak | SHA-256 |
|---|---|
| `frontend/dist/index.html` | `58de33ae53984d40fdaef9d7b465884d2debfea7292d113fba42c2d894c303b8` |
| `frontend/dist/assets/index-CsAmeAMs.js` | `ceafd78e7b836264ce79343fe276604f1c937cc4386d3c2c66b0316ce5d62d44` |
| `frontend/dist/assets/index-DhUhnmx-.css` | `6106229c881854f3e373d56050bc560af8f56b00fc69b6830d5cef40a2395c74` |

## Capture manifest

Capture dilakukan melalui browser lokal terhadap build di atas. Fingerprint mengikat byte capture dengan route dan state yang diamati; capture tidak berisi dokumen pengguna.

| Route | SHA-256 capture | Byte | Heading/state utama |
|---|---|---:|---|
| `#/smart-upload` | `1384faae4ba93dfe801f9a3ad29281247146ec34605b7f1b56da3adb08925384` | 82.925 | Upload Evidence Pintar; Operational Readiness; stage efektif `development`; rule 0/920; shadow ditahan; Vision/OCR aktif; alert 0 |
| `#/visual-review` | `6566510b0fd31c2d2db8b0469f1c108609456566279c90cb6ed57f12a2ddc821` | 63.056 | Review Visual; empat filter; empty state 0/0 pada database fixture |
| `#/governance` | `029465592bee75b597953d6ec222e1fac7168f602b28efbbab193e3dc1c81a4b` | 90.314 | Rule Parameter, Dataset Ahli, Bukti Rilis, OCR/Vision; fail-closed; 184 parameter belum diperiksa |

Route `#/guided-review` juga diverifikasi melalui DOM: heading **Wizard Review Terpandu**, export hasil, empat filter, dan empty state 0/0 tampil. Capture viewport route tersebut melewati batas waktu screenshot browser, sehingga tidak diberi fingerprint visual dan tidak diklaim sebagai capture berhasil.

## Acceptance baseline

- Navigasi Upload Pintar, Review Terpandu, Review Visual, dan Governance V2 tersedia dari header.
- Upload Pintar menampilkan rangkaian multi-engine dan readiness, bukan progress timer simulasi.
- Rollout guard menampilkan `development` ketika rule/expert gate belum terpenuhi.
- Review Visual dan Review Terpandu mempunyai empty state yang dapat dipahami pengguna nonteknis.
- Governance menampilkan 184 parameter dan lima grade per parameter tanpa membutuhkan JSON/checksum manual.
- Tidak ada error console pada sesi capture.

Baseline ini adalah bukti layout/state build lokal, bukan sign-off product owner atau bukti canary produksi. Capture berikutnya harus mencatat build fingerprint baru dan menjelaskan perubahan visual terhadap manifest ini.

## QA inkremental typed-region

Build setelah penambahan overlay OOXML dan penandaan region manual:

| Artefak | SHA-256 |
|---|---|
| `frontend/dist/index.html` | `4773961632af149d07d36c0a917953cd6cd694de385536bdc2736709814350b3` |
| `frontend/dist/assets/index-BWJ279uU.js` | `ea3cddc4685b00b14cd5aef5b68a72100b55e09dbf6a5bb168f9648ce2bfe2b5` |
| `frontend/dist/assets/index-CkJPxVyT.css` | `97269d611fef7e037b7b9bad2a1fe395e150b5b51a5b91b21bef74b0d9c98376` |

Browser QA lokal pada database synthetic terpisah membuktikan:

- pilihan hasil review membuka editor region tanpa pemrograman;
- reviewer dapat memilih `Stempel/cap`, mengisi label, lalu menarik kotak pada gambar;
- koordinat normalized ditampilkan kembali (`x/y/width/height`), overlay bernomor terlihat, dan region dapat dihapus sebelum disimpan;
- penyimpanan append-only menaikkan progres menjadi 1/1, menampilkan history, checksum siap diterapkan, dan tidak mengubah run sumber;
- request run turunan selesai, sedangkan fixture yang sengaja menghasilkan OCR berbeda ditolak sebagai stale dan tetap fail-closed;
- tidak ada console error pada sesi tersebut.

Capture screenshot baru tidak diklaim pada QA inkremental ini; fingerprint capture pada manifest sebelumnya tetap mengacu ke build baseline awal.

## QA inkremental storage-attestation readiness

Build setelah hard gate attestation storage dan status readiness yang tidak lagi menyebut filesystem sebagai terenkripsi hanya karena backend-nya `filesystem`:

| Artefak | SHA-256 |
|---|---|
| `frontend/dist/index.html` | `986eddce321ed52b67008814fdf6bdebd6e8f9b4cb046a27e562a89363f2b1c9` |
| `frontend/dist/assets/index-D1rk9Vdr.js` | `0739acb6f5cd9aa17fa54fd80f005778de2bf0ec8c76a13c693946bc90a5040b` |
| `frontend/dist/assets/index-BWpZ8v9b.css` | `73c55bbf67816534a7013e5fe9ed4b9bf7583998d7a6eed7dffb83b74d4bfa88` |

Boundary verifier dan Vite production build lulus. Kartu readiness kini menampilkan **Attestation valid** hanya ketika signature, expiry, owner/permission, serta binding path/device lulus; selain itu menampilkan **Bukti belum valid** dan jumlah pemeriksaan gagal. Tidak ada capture browser baru yang diklaim untuk perubahan teks kecil ini; manifest screenshot awal tetap merupakan baseline visual terakhir.

## QA inkremental Compute Routing

Build setelah Execution Trace menampilkan route, skor kompleksitas, dan skor risiko:

| Artefak | SHA-256 |
|---|---|
| `frontend/dist/index.html` | `681f033e4c56ad3fdb7303f7bfa4e114b3182695bff2efef220aab44d3e29ca6` |
| `frontend/dist/assets/index-BmXwrEze.js` | `203aa569635807c8eb4800edb59a4578dd9f4727ca22458264d8054bf090e922` |
| `frontend/dist/assets/index-BWpZ8v9b.css` | `73c55bbf67816534a7013e5fe9ed4b9bf7583998d7a6eed7dffb83b74d4bfa88` |

Boundary verifier dan Vite production build lulus. Kartu `compute_routing_fact`, `compute_routing_mapping`, dan `compute_routing_verification` menampilkan target route, apakah route dipilih, serta skor 0–100 yang secara eksplisit disebut kompleksitas/risiko dan bukan confidence. Tidak ada capture browser baru yang diklaim; fingerprint screenshot awal tetap baseline visual terakhir.

## QA inkremental OCR safe budget

Build setelah kartu Governance OCR menampilkan hierarchical resource envelope:

| Artefak | SHA-256 |
|---|---|
| `frontend/dist/index.html` | `855f46133ea27326429988099b1259f048e2d779dd4159f7fd4e14881139c118` |
| `frontend/dist/assets/index-k3fOm3pu.js` | `cf8f8e3af1b2273eef5bb4f7aa8a4995b065ad2a08abd3b23d776944b620a36a` |
| `frontend/dist/assets/index-BWpZ8v9b.css` | `73c55bbf67816534a7013e5fe9ed4b9bf7583998d7a6eed7dffb83b74d4bfa88` |

Boundary verifier dan Vite production build lulus. Kartu Local OCR kini menampilkan ukuran render batch serta batas attempt, unit, dan dokumen yang dikirim runtime; angka tersebut bukan progress atau confidence. Tidak ada capture browser baru yang diklaim; fingerprint screenshot awal tetap baseline visual terakhir.

## QA inkremental durable checkpoint

Build setelah Operational Readiness menampilkan kebijakan pemulihan Visual/OCR:

| Artefak | SHA-256 |
|---|---|
| `frontend/dist/index.html` | `3ac35babfdaf92f0f1e22786d2651af4ebb70cc166b636dee628976170656984` |
| `frontend/dist/assets/index-C8xZAUw6.js` | `33bd0e8a6734f650e72f8c30d1ee7427472850e1705daf401ae1bb7d7bf42ff4` |
| `frontend/dist/assets/index-BWpZ8v9b.css` | `73c55bbf67816534a7013e5fe9ed4b9bf7583998d7a6eed7dffb83b74d4bfa88` |

Boundary verifier dan Vite production build lulus. Browser QA lokal pada `#/smart-upload` membuktikan kartu **Pemulihan OCR** menampilkan **Durable per batch**, `unit-checkpoint-v2`, dan `checksum-bound resume`; readiness API dan seluruh asset mengembalikan HTTP 200 serta tidak ada console warning/error. Tidak ada screenshot baru yang diklaim; verifikasi menggunakan DOM aktual build di atas dan fingerprint screenshot awal tetap baseline visual terakhir.

## QA inkremental worker traffic readiness

Build setelah Operational Readiness menampilkan state worker yang dapat menerima job:

| Artefak | SHA-256 |
|---|---|
| `frontend/dist/index.html` | `6dcc7b65da17e09015d98baebf6cf114d71f40f5a0e4c9101a912b24fb3bba09` |
| `frontend/dist/assets/index-p8a9H2ph.js` | `17f2be344507fc2596f43ca065c8e231119da01e3f53c5adbfcc45d6c29cf2a3` |
| `frontend/dist/assets/index-BWpZ8v9b.css` | `73c55bbf67816534a7013e5fe9ed4b9bf7583998d7a6eed7dffb83b74d4bfa88` |

Boundary verifier dan Vite production build lulus. Kartu **Worker V2** membedakan **Menerima job**, **Menghentikan**, **Draining**, dan **Ditahan**, serta menampilkan status leader lease dan queue backend tanpa nama atau isi dokumen. Docker image backend benar-benar mencapai status `healthy` dengan V2 aktif; `/api/health/live` dan `/api/health/ready` mengembalikan state yang sesuai. Tidak ada screenshot baru yang diklaim; fingerprint screenshot awal tetap baseline visual terakhir.

## QA inkremental controlled-upload idempotency

Build setelah UI berpindah ke endpoint roadmap `approve-upload`, membedakan respons sukses baru dari retry idempotent, dan menampilkan state reservation pada mapping/readiness:

| Artefak | SHA-256 |
|---|---|
| `frontend/dist/index.html` | `e920252948ac524332bb0bbd85d620da0503ed4648e576402a652378685095e1` |
| `frontend/dist/assets/index-D1p6tIxI.js` | `6250fd1ee1510b70044759add51b40b762241825a16139871d0ccde095b997b3` |
| `frontend/dist/assets/index-Dmgjl5cn.css` | `a6d1854ecd153b125a473f44aae1a72861acd8f72fa183d70d70a816cc39869c` |

Boundary verifier dan Vite production build lulus. Respons `idempotent=true` menampilkan bahwa action audit lama digunakan tanpa upload ulang. Mapping dengan status `uploading`, `uploaded_primary`, atau `blocked_ambiguous` menampilkan action/legacy-review ID, mengunci tombol berbahaya, dan menyediakan refresh hanya untuk reservation aktif. Operational Readiness menampilkan **Reservation upload — Aman** dengan hitungan aktif/stale serta mitigation reconciliation. Browser QA aktual pada 1265 px membuktikan tepat satu kartu dan satu mitigation tampil, `horizontalOverflow=false`, serta tidak ada console warning/error. Screenshot QA baru diperiksa untuk layout tetapi tidak disimpan sebagai baseline capture formal; fingerprint capture awal tetap baseline visual terakhir.

## QA inkremental controlled-upload two-person reconciliation

Build setelah rekonsiliasi hasil terminal ambigu dipisahkan menjadi ledger append-only dengan dua reviewer:

| Artefak | Ukuran | SHA-256 |
|---|---:|---|
| `frontend/dist/index.html` | 409 byte | `5fa60f27ec01a2b284fcdb5f4317499855cb60e2cd8543b02e04c6732f98d7f2` |
| `frontend/dist/assets/index-Dx2rkAlR.js` | 352.708 byte | `e4a5e18e4b18d25f26d2b14ea7afb881bc6bc71db5b291f2075389bc7da6a825` |
| `frontend/dist/assets/index-YPO12YNf.css` | 68.343 byte | `e33a9109b66df6e687a4d1e42f515c7222b032d57bf509ef1294a33b35052282` |

Boundary verifier dan Vite production build lulus. Browser QA aktual pada 1265 px mula-mula membuktikan state `0 ambiguity terbuka` tetap **Aman**. Setelah satu action `blocked_ambiguous` disisipkan hanya ke database QA sementara, reload aktual menampilkan **Perlu rekonsiliasi**, `1 ambiguity terbuka`, dan mitigation blocked yang melarang retry otomatis. Kedua state mempunyai `horizontalOverflow=false` dan sesi tidak mencatat console warning/error. Form no-code dan ledger dua-reviewer diverifikasi oleh integration regression; browser QA ini hanya mengklaim rendering kartu readiness, bukan pengesahan manusia atau capture baseline formal baru.

## QA inkremental evaluation contract V29–V30

Build V30 setelah penambahan evidence-role provenance, expert expected-template label, dan metrik evaluasi roadmap:

| Artefak | Ukuran | SHA-256 |
|---|---:|---|
| `frontend/dist/index.html` | 409 byte | `c8e30147df59ce6ac37702de9e343ae1a4a24579a5b7f1209ccc91957acff92d` |
| `frontend/dist/assets/index-BGu1X_HF.js` | 356.520 byte | `17bd0900d069c399181af39266afe66644498b5943c978ccd36a73730ed934dd` |
| `frontend/dist/assets/index-YPO12YNf.css` | 68.343 byte | `e33a9109b66df6e687a4d1e42f515c7222b032d57bf509ef1294a33b35052282` |

Boundary verifier dan Vite production build lulus. Browser QA formal V30 pada 1265 px membuktikan Guided Review menampilkan role sistem sebagai advisory, reviewer dapat memilih role `primary`, dan tombol simpan tetap nonaktif sampai status template dipilih. Setelah reviewer memilih `substantive`, keputusan tersimpan sebagai kandidat label ahli; Dataset Ahli menampilkan `confirmed`, role `primary`, status template `substantive`, coverage 100%, expected mapping, dan source location tanpa melewati pengesahan dua orang. Bukti Rilis menampilkan mapping precision, source accuracy, evidence-role accuracy/coverage, template accuracy/recall/coverage, grade, assessment, abstention, latency, dan cost dari report informational. Layout Guided Review, Dataset Ahli, dan Bukti Rilis tidak overflow horizontal; console mencatat nol warning/error. QA memakai fixture/database sementara, tidak mengklaim pengesahan manusia, release authority, atau capture baseline visual baru.
