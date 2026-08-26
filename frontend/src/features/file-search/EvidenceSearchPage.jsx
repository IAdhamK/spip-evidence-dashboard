import { useRef, useState } from "react";
import {
  ArrowLeft,
  Check,
  Clipboard,
  ExternalLink,
  FileSearch,
  FileText,
  Loader2,
  Search,
} from "lucide-react";
import { apiGet } from "../../lib/api.js";
import { formatBytes, formatDate } from "../../lib/formatters.js";
import { canonicalLumbungUrl } from "../../lib/lumbung-link.js";
import { EmptyState, Notice } from "../shared/Feedback.jsx";
import {
  FILE_SEARCH_SUGGESTIONS,
  FILE_TYPE_OPTIONS,
  buildFileSearchPath,
  evidenceLocationLabel,
  fileSearchPromptState,
  fileTypeLabel,
  secondaryFileName,
} from "./file-search.js";


export default function EvidenceSearchPage({ onBack, onOpenDetail, kkOptions = [] }) {
  const [query, setQuery] = useState("");
  const [kkId, setKkId] = useState("all");
  const [fileType, setFileType] = useState("all");
  const [grade, setGrade] = useState("all");
  const [response, setResponse] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [copiedId, setCopiedId] = useState(null);
  const requestRevision = useRef(0);

  async function runSearch(nextQuery = query) {
    const normalizedQuery = String(nextQuery ?? "").trim();
    setQuery(normalizedQuery);
    if (normalizedQuery.length < 2) {
      setResponse(null);
      setError(normalizedQuery ? "Masukkan minimal dua karakter pencarian." : "Masukkan kata yang ingin dicari.");
      return;
    }
    const revision = ++requestRevision.current;
    setLoading(true);
    setError("");
    try {
      const result = await apiGet(buildFileSearchPath({ query: normalizedQuery, kkId, fileType, grade }));
      if (revision === requestRevision.current) setResponse(result);
    } catch (searchError) {
      if (revision === requestRevision.current) setError(searchError.message);
    } finally {
      if (revision === requestRevision.current) setLoading(false);
    }
  }

  async function copyName(item) {
    if (!navigator.clipboard?.writeText) {
      setError("Browser belum mengizinkan penyalinan otomatis. Salin nama file secara manual.");
      return;
    }
    try {
      await navigator.clipboard.writeText(item.base_name);
      setCopiedId(item.id);
      window.setTimeout(() => setCopiedId(null), 1600);
    } catch {
      setError("Nama file belum dapat disalin. Periksa izin clipboard browser.");
    }
  }

  const prompt = fileSearchPromptState(query, response, loading);
  const results = response?.results ?? [];

  return (
    <section className="evidence-search-page">
      <button className="back-button" type="button" onClick={onBack}>
        <ArrowLeft size={18} />
        Kembali ke Daftar Subunsur
      </button>

      <header className="evidence-search-header">
        <div className="evidence-search-heading-icon"><FileSearch size={28} /></div>
        <div>
          <p className="eyebrow">Indeks hasil sinkronisasi Lumbung File</p>
          <h2>Cari Evidence</h2>
          <p>Cari berdasarkan nama file, nomor dokumen, kegiatan, atau kata kunci administrasi.</p>
        </div>
      </header>

      <form
        className="evidence-search-form"
        onSubmit={(event) => {
          event.preventDefault();
          runSearch();
        }}
      >
        <label className="evidence-search-input">
          <Search size={20} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Contoh: IKPA, KPPN, Petris, 465, atau laporan risiko"
            autoFocus
          />
        </label>
        <button className="primary-button evidence-search-submit" type="submit" disabled={loading}>
          {loading ? <Loader2 className="spin" size={18} /> : <Search size={18} />}
          Cari
        </button>

        <div className="evidence-search-filters">
          <label>
            <span>Kelompok KK</span>
            <select value={kkId} onChange={(event) => setKkId(event.target.value)}>
              <option value="all">Semua KK</option>
              {kkOptions.map((option) => <option value={option} key={option}>{option}</option>)}
            </select>
          </label>
          <label>
            <span>Jenis file</span>
            <select value={fileType} onChange={(event) => setFileType(event.target.value)}>
              {FILE_TYPE_OPTIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}
            </select>
          </label>
          <label>
            <span>Grade</span>
            <select value={grade} onChange={(event) => setGrade(event.target.value)}>
              <option value="all">Semua Grade</option>
              {["E", "D", "C", "B", "A"].map((value) => <option value={value} key={value}>Grade {value}</option>)}
            </select>
          </label>
        </div>
      </form>

      <div className="evidence-search-suggestions" aria-label="Saran kata kunci">
        <span>Saran cepat</span>
        <div>
          {FILE_SEARCH_SUGGESTIONS.map((suggestion) => (
            <button type="button" key={suggestion} onClick={() => runSearch(suggestion)}>
              {suggestion}
            </button>
          ))}
        </div>
      </div>

      <Notice
        tone="info"
        text="Pencarian ini membaca nama dan lokasi file dari sinkronisasi terakhir. Isi dokumen belum dibaca dan hasil tidak menetapkan Grade."
      />
      {error ? <Notice tone="danger" text={error} /> : null}

      <section className="evidence-search-results" aria-live="polite">
        <div className="section-heading">
          <div>
            <h3>Hasil Pencarian</h3>
            <p className={`search-prompt search-prompt-${prompt.kind}`}>{prompt.text}</p>
          </div>
          {response?.expanded_terms?.length ? (
            <div className="expanded-keywords">
              Padanan: {response.expanded_terms.join(", ")}
            </div>
          ) : null}
        </div>

        {results.length ? (
          <div className="evidence-result-list">
            {results.map((item) => {
              const publicUrl = canonicalLumbungUrl(item.public_url, item.location_path);
              return (
                <article className="evidence-result-card" key={item.id}>
                  <div className="evidence-result-icon"><FileText size={21} /></div>
                  <div className="evidence-result-content">
                    <div className="evidence-result-title">
                      <div>
                        <h4>{item.base_name}</h4>
                        <p>{evidenceLocationLabel(item)}</p>
                      </div>
                      <span className="file-type-tag">{fileTypeLabel(item.file_type)}</span>
                    </div>
                    <p className="evidence-result-subunsur">{item.subunsur_name}</p>
                    <p className="evidence-result-path" title={item.remote_path}>{secondaryFileName(item)}</p>
                    <div className="evidence-result-meta">
                      <span>{formatBytes(item.size_bytes)}</span>
                      <span>Diubah {formatDate(item.modified_at)}</span>
                      <span>{item.match_reason}</span>
                    </div>
                    <div className="evidence-result-actions">
                      {publicUrl ? (
                        <a className="primary-button link-button" href={publicUrl} target="_blank" rel="noreferrer">
                          <ExternalLink size={16} />
                          Buka Lokasi Lumbung
                        </a>
                      ) : null}
                      <button className="secondary-button" type="button" onClick={() => onOpenDetail(item)}>
                        <FileSearch size={16} />
                        Lihat Subunsur
                      </button>
                      <button className="quiet-button" type="button" onClick={() => copyName(item)}>
                        {copiedId === item.id ? <Check size={16} /> : <Clipboard size={16} />}
                        {copiedId === item.id ? "Tersalin" : "Salin Nama"}
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        ) : !loading && response ? (
          <EmptyState text="Coba kata yang lebih singkat, nomor dokumen, atau salah satu saran cepat di atas." />
        ) : null}
      </section>
    </section>
  );
}
