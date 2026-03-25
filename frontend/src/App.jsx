import { useState } from "react";
import { getPaper, startAnalysis, startCodeAnalysis, getCodeAnalysis } from "./api";
import SearchPanel from "./components/SearchPanel";
import ProgressPanel from "./components/ProgressPanel";
import PreviewPanel from "./components/PreviewPanel";
import ChatPanel from "./components/ChatPanel";
import LibraryPanel from "./components/LibraryPanel";

export default function App() {
  // 左侧 tab：独立于分析状态
  const [sidebarTab, setSidebarTab] = useState("search"); // "search" | "library"

  // 分析状态：独立于 tab，切换 tab 不会丢失
  const [analysisPhase, setAnalysisPhase] = useState("idle"); // "idle" | "analyzing" | "done"
  const [progressMessages, setProgressMessages] = useState([]);

  const [currentPaperId, setCurrentPaperId] = useState(null);
  const [analysisContent, setAnalysisContent] = useState("");
  const [codeAnalysisContent, setCodeAnalysisContent] = useState("");
  const [chatInitialMsg, setChatInitialMsg] = useState("");

  async function loadPaper(arxivId) {
    try {
      const data = await getPaper(arxivId);
      setCurrentPaperId(arxivId);
      setAnalysisContent(data.analysis || "");
      const codeContent = await getCodeAnalysis(arxivId);
      setCodeAnalysisContent(codeContent || "");
    } catch (e) {
      console.error(e);
    }
  }

  function handleStartAnalysis(payload) {
    setProgressMessages([]);
    setAnalysisPhase("analyzing");
    setSidebarTab("search"); // 切回搜索 tab 显示进度

    startAnalysis(
      payload,
      (event) => {
        setProgressMessages((prev) => [...prev, event.message]);
      },
      (event) => {
        const arxivId = event.arxiv_id || payload.arxiv_id;
        // 如果勾选了代码分析且提供了 GitHub URL，进入第二阶段
        if (payload.code && payload.github_url) {
          setProgressMessages((prev) => [...prev, "── 开始代码分析阶段 ──"]);
          startCodeAnalysis(
            arxivId,
            { github_url: payload.github_url, sections: payload.code_sections },
            (ev) => {
              setProgressMessages((prev) => [...prev, ev.message]);
            },
            () => {
              setAnalysisPhase("done");
              setProgressMessages((prev) => [...prev, "✅ 代码分析完成！"]);
              loadPaper(arxivId);
            },
            (err) => {
              setProgressMessages((prev) => [...prev, `❌ 代码分析错误: ${err}`]);
              setAnalysisPhase("done");
              loadPaper(arxivId);
            }
          );
        } else {
          setAnalysisPhase("done");
          setProgressMessages((prev) => [...prev, "✅ 分析完成！"]);
          loadPaper(arxivId);
        }
      },
      (err) => {
        setProgressMessages((prev) => [...prev, `❌ 错误: ${err}`]);
        setAnalysisPhase("done");
      }
    );
  }

  function handleAnalysisUpdated() {
    if (currentPaperId) loadPaper(currentPaperId);
  }

  function handleEditRequest() {
    setChatInitialMsg("请修改分析文章：");
  }

  // 搜索 tab 下显示什么：分析中/完成 → ProgressPanel，否则 → SearchPanel
  const showProgress = sidebarTab === "search" && analysisPhase !== "idle";

  return (
    <div className="app">
      {/* Left sidebar */}
      <aside className="sidebar">
        <div className="sidebar-tabs">
          <button
            className={sidebarTab === "search" ? "active" : ""}
            onClick={() => setSidebarTab("search")}
          >
            搜索
            {analysisPhase === "analyzing" && (
              <span className="tab-badge">●</span>
            )}
          </button>
          <button
            className={sidebarTab === "library" ? "active" : ""}
            onClick={() => setSidebarTab("library")}
          >
            论文库
          </button>
        </div>

        <div className="sidebar-content">
          {sidebarTab === "search" && (
            showProgress
              ? <ProgressPanel messages={progressMessages} done={analysisPhase === "done"} />
              : <SearchPanel onStartAnalysis={handleStartAnalysis} />
          )}
          {sidebarTab === "library" && (
            <LibraryPanel
              onSelectPaper={loadPaper}
              selectedId={currentPaperId}
            />
          )}
        </div>
      </aside>

      {/* Main content — Preview */}
      <main className="main-content">
        <PreviewPanel
          arxivId={currentPaperId}
          content={analysisContent}
          codeContent={codeAnalysisContent}
          onEditRequest={handleEditRequest}
        />
      </main>

      {/* Right sidebar — Chat */}
      <aside className="chat-sidebar">
        <ChatPanel
          arxivId={currentPaperId}
          initialMessage={chatInitialMsg}
          onAnalysisUpdated={handleAnalysisUpdated}
        />
      </aside>
    </div>
  );
}
