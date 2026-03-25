import { useEffect, useRef, useState } from "react";
import MarkdownIt from "markdown-it";
import mk from "markdown-it-katex";

const md = new MarkdownIt({ html: true, linkify: true, typographer: true }).use(mk);

function renderContent(content, arxivId) {
  if (!content) return "";
  let processed = content;
  if (arxivId) {
    processed = processed.replace(
      /!\[([^\]]*)\]\(images\/([^)]+)\)/g,
      `![$1](/api/paper/${arxivId}/images/$2)`
    );
  }
  return md.render(processed);
}

export default function PreviewPanel({ arxivId, content, codeContent, onEditRequest }) {
  const [activeTab, setActiveTab] = useState("analysis");
  const containerRef = useRef(null);

  // 当 codeContent 首次出现时自动切换到代码解读 tab
  useEffect(() => {
    if (codeContent) setActiveTab("code");
  }, [codeContent]);

  // 切换论文时重置到分析报告 tab
  useEffect(() => {
    setActiveTab("analysis");
  }, [arxivId]);

  if (!content) {
    return (
      <div className="preview-panel empty">
        <div className="empty-state">
          <div className="empty-icon">📄</div>
          <p>选择或分析一篇论文后，这里将显示分析报告</p>
        </div>
      </div>
    );
  }

  const activeContent = activeTab === "code" ? codeContent : content;
  const rendered = renderContent(activeContent, arxivId);

  return (
    <div className="preview-panel">
      <div className="preview-header">
        <div className="preview-tabs">
          <button
            className={`preview-tab-btn ${activeTab === "analysis" ? "active" : ""}`}
            onClick={() => setActiveTab("analysis")}
          >
            分析报告
          </button>
          {codeContent && (
            <button
              className={`preview-tab-btn ${activeTab === "code" ? "active" : ""}`}
              onClick={() => setActiveTab("code")}
            >
              代码解读
            </button>
          )}
        </div>
        {onEditRequest && activeTab === "analysis" && (
          <button className="edit-btn" onClick={onEditRequest}>
            编辑指令
          </button>
        )}
      </div>
      <div
        ref={containerRef}
        className="markdown-body"
        dangerouslySetInnerHTML={{ __html: rendered }}
      />
    </div>
  );
}
