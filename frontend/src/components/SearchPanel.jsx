import { useState, useEffect, useRef } from "react";
import { searchPapers, resolvePaper, getTags } from "../api";

const STYLES = [
  { id: "academic", label: "学术型", desc: "专业严谨，术语准确" },
  { id: "storytelling", label: "故事型", desc: "从直觉出发，用比喻和例子" },
  { id: "concise", label: "精炼型", desc: "直击核心，快速阅读" },
];

export default function SearchPanel({ onStartAnalysis }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [selected, setSelected] = useState(null);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [style, setStyle] = useState("academic");
  const [formula, setFormula] = useState(false);
  const [code, setCode] = useState(false);
  const [tagInput, setTagInput] = useState("");
  const [tags, setTags] = useState([]);
  const [githubUrl, setGithubUrl] = useState("");
  const [codeSections, setCodeSections] = useState(["模型架构", "训练流程", "主要创新"]);
  const [existingTags, setExistingTags] = useState([]);
  const [suggestions, setSuggestions] = useState([]);
  const [activeSuggestion, setActiveSuggestion] = useState(-1);
  const [error, setError] = useState("");
  const tagInputRef = useRef(null);
  const suggestionsRef = useRef(null);

  useEffect(() => {
    getTags().then(setExistingTags);
  }, []);

  async function handleSearch(e) {
    e.preventDefault();
    if (!query.trim()) return;
    setError("");
    setSearching(true);
    setSearched(false);
    setSelected(null);
    setResults([]);
    try {
      // Check if it's a URL or arxiv ID
      if (query.includes("arxiv.org") || /^\d{4}\.\d+/.test(query.trim())) {
        const info = await resolvePaper(query.trim());
        setResults([info]);
        setSelected(info);
      } else {
        const data = await searchPapers(query.trim());
        setResults(data.results || []);
      }
    } catch (err) {
      setError("搜索失败: " + err.message);
    } finally {
      setSearching(false);
      setSearched(true);
    }
  }

  function handleTagInputChange(e) {
    const val = e.target.value;
    setTagInput(val);
    setActiveSuggestion(-1);
    if (val.trim()) {
      const q = val.trim().toLowerCase();
      const filtered = existingTags.filter(
        (t) => t.toLowerCase().includes(q) && !tags.includes(t)
      );
      setSuggestions(filtered);
    } else {
      setSuggestions([]);
    }
  }

  function commitTag(t) {
    const clean = t.trim().replace(/,$/, "");
    if (clean && !tags.includes(clean)) setTags((prev) => [...prev, clean]);
    setTagInput("");
    setSuggestions([]);
    setActiveSuggestion(-1);
    tagInputRef.current?.focus();
  }

  function addTag(e) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveSuggestion((i) => Math.min(i + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveSuggestion((i) => Math.max(i - 1, -1));
    } else if (e.key === "Escape") {
      setSuggestions([]);
      setActiveSuggestion(-1);
    } else if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      if (activeSuggestion >= 0 && suggestions[activeSuggestion]) {
        commitTag(suggestions[activeSuggestion]);
      } else if (tagInput.trim()) {
        commitTag(tagInput);
      }
    }
  }

  function removeTag(t) {
    setTags(tags.filter((x) => x !== t));
  }

  function handleAnalyze() {
    if (!selected) return;
    if (code && !githubUrl.trim()) {
      setError("请输入 GitHub 仓库地址");
      return;
    }
    setError("");
    onStartAnalysis({
      arxiv_id: selected.arxiv_id,
      style,
      formula,
      code,
      github_url: githubUrl.trim(),
      code_sections: codeSections,
      tags,
    });
  }

  return (
    <div className="search-panel">
      <h2>搜索论文</h2>

      <form onSubmit={handleSearch} className="search-form">
        <input
          type="text"
          placeholder="输入关键词或 arxiv URL/ID..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button type="submit" disabled={searching}>
          {searching ? "搜索中..." : "搜索"}
        </button>
      </form>

      {error && <div className="error">{error}</div>}

      {searched && !searching && !error && results.length === 0 && (
        <div className="no-results">未找到相关论文，请尝试其他关键词或直接输入 arxiv URL/ID</div>
      )}

      {results.length > 0 && (
        <div className="results-list">
          {results.map((r) => (
            <div
              key={r.arxiv_id}
              className={`result-card ${selected?.arxiv_id === r.arxiv_id ? "selected" : ""}`}
              onClick={() => setSelected(r)}
            >
              <div className="result-title">{r.title}</div>
              <div className="result-meta">
                {r.authors?.slice(0, 3).join(", ")}
                {r.authors?.length > 3 ? " 等" : ""} · {r.published}
              </div>
              <div className="result-abstract">{r.abstract?.slice(0, 150)}...</div>
            </div>
          ))}
        </div>
      )}

      {selected && (
        <div className="config-panel">
          <h3>配置分析选项</h3>
          <div className="selected-paper">
            <strong>{selected.title}</strong>
            <span className="arxiv-id">arxiv:{selected.arxiv_id}</span>
          </div>

          <div className="config-section">
            <label>写作风格</label>
            <div className="style-options">
              {STYLES.map((s) => (
                <button
                  key={s.id}
                  className={`style-btn ${style === s.id ? "active" : ""}`}
                  onClick={() => setStyle(s.id)}
                >
                  <span className="style-label">{s.label}</span>
                  <span className="style-desc">{s.desc}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="config-section">
            <div className="toggles">
              <label className="toggle-item">
                <input
                  type="checkbox"
                  checked={formula}
                  onChange={(e) => setFormula(e.target.checked)}
                />
                公式讲解
              </label>
              <label className="toggle-item">
                <input
                  type="checkbox"
                  checked={code}
                  onChange={(e) => setCode(e.target.checked)}
                />
                代码分析
              </label>
            </div>

            {code && (
              <div className="code-config-section">
                <input
                  type="text"
                  className="github-url-input"
                  placeholder="https://github.com/owner/repo"
                  value={githubUrl}
                  onChange={(e) => setGithubUrl(e.target.value)}
                />
                <div className="code-sections-label">分析内容</div>
                <div className="code-sections-row">
                  {["模型架构", "训练流程", "主要创新"].map((name) => (
                    <label key={name} className="toggle-item">
                      <input
                        type="checkbox"
                        checked={codeSections.includes(name)}
                        onChange={() =>
                          setCodeSections((prev) =>
                            prev.includes(name)
                              ? prev.filter((s) => s !== name)
                              : [...prev, name]
                          )
                        }
                      />
                      {name}
                    </label>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="config-section">
            <label>标签</label>
            <div className="tags-container">
              {tags.map((t) => (
                <span key={t} className="tag">
                  {t}
                  <button onClick={() => removeTag(t)}>×</button>
                </span>
              ))}
              <div className="tag-input-wrap">
                <input
                  ref={tagInputRef}
                  type="text"
                  placeholder="输入标签后按 Enter..."
                  value={tagInput}
                  onChange={handleTagInputChange}
                  onKeyDown={addTag}
                  onBlur={() => setTimeout(() => setSuggestions([]), 150)}
                />
                {suggestions.length > 0 && (
                  <div className="tag-suggestions" ref={suggestionsRef}>
                    {suggestions.map((s, i) => (
                      <div
                        key={s}
                        className={`tag-suggestion-item${i === activeSuggestion ? " active" : ""}`}
                        onMouseDown={() => commitTag(s)}
                      >
                        {s}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>

          <button className="analyze-btn" onClick={handleAnalyze}>
            开始分析
          </button>
        </div>
      )}
    </div>
  );
}
