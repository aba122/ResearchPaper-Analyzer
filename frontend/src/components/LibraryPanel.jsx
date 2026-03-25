import { useEffect, useState } from "react";
import { listPapers } from "../api";

export default function LibraryPanel({ onSelectPaper, selectedId }) {
  const [papers, setPapers] = useState([]);
  const [activeTag, setActiveTag] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadPapers();
  }, []);

  async function loadPapers() {
    try {
      const data = await listPapers();
      setPapers(data.papers || []);
    } catch {}
    setLoading(false);
  }

  // Collect all tags
  const allTags = [...new Set(papers.flatMap((p) => p.tags || []))];

  const filtered = activeTag
    ? papers.filter((p) => (p.tags || []).includes(activeTag))
    : papers;

  return (
    <div className="library-panel">
      <div className="library-header">
        <h2>论文库</h2>
        <button className="refresh-btn" onClick={loadPapers} title="刷新">↻</button>
      </div>

      {allTags.length > 0 && (
        <div className="tag-filters">
          <button
            className={`tag-filter ${!activeTag ? "active" : ""}`}
            onClick={() => setActiveTag(null)}
          >
            全部
          </button>
          {allTags.map((t) => (
            <button
              key={t}
              className={`tag-filter ${activeTag === t ? "active" : ""}`}
              onClick={() => setActiveTag(activeTag === t ? null : t)}
            >
              {t}
            </button>
          ))}
        </div>
      )}

      <div className="paper-list">
        {loading && <p className="loading-text">加载中...</p>}
        {!loading && filtered.length === 0 && (
          <p className="empty-text">暂无论文，请先搜索并分析一篇论文</p>
        )}
        {filtered.map((p) => (
          <div
            key={p.arxiv_id}
            className={`paper-card ${selectedId === p.arxiv_id ? "selected" : ""}`}
            onClick={() => onSelectPaper(p.arxiv_id)}
          >
            <div className="paper-title">{p.title}</div>
            <div className="paper-meta">
              <span className="paper-id">arxiv:{p.arxiv_id}</span>
              <span className="paper-style">{p.style}</span>
            </div>
            {p.tags?.length > 0 && (
              <div className="paper-tags">
                {p.tags.map((t) => (
                  <span key={t} className="tag">{t}</span>
                ))}
              </div>
            )}
            <div className="paper-date">{p.analyzed_at?.slice(0, 10)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
