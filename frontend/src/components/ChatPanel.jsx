import { useEffect, useRef, useState } from "react";
import { chatWithAgent } from "../api";
import MarkdownIt from "markdown-it";

const md = new MarkdownIt({ html: false, linkify: true });

export default function ChatPanel({ arxivId, initialMessage, onAnalysisUpdated }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const endRef = useRef(null);

  // Pre-fill from edit request
  useEffect(() => {
    if (initialMessage) setInput(initialMessage);
  }, [initialMessage]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  function sendMessage(e) {
    e?.preventDefault();
    const text = input.trim();
    if (!text || loading || !arxivId) return;
    setInput("");

    const userMsg = { role: "user", content: text };
    const assistantMsg = { role: "assistant", content: "" };
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setLoading(true);

    const history = messages.map((m) => ({ role: m.role, content: m.content }));

    chatWithAgent(
      arxivId,
      text,
      history,
      (chunk) => {
        setMessages((prev) => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          if (last.role === "assistant") {
            updated[updated.length - 1] = { ...last, content: last.content + chunk };
          }
          return updated;
        });
      },
      () => {
        setLoading(false);
        // If message contained update instructions, refresh analysis
        if (text.includes("修改") || text.includes("添加") || text.includes("删除")) {
          onAnalysisUpdated?.();
        }
      },
      (err) => {
        setLoading(false);
        setMessages((prev) => {
          const updated = [...prev];
          updated[updated.length - 1] = { role: "assistant", content: `错误: ${err}` };
          return updated;
        });
      }
    );
  }

  if (!arxivId) {
    return (
      <div className="chat-panel empty">
        <p>选择一篇论文后可与 Agent 交互</p>
      </div>
    );
  }

  return (
    <div className="chat-panel">
      <h3>与 Agent 对话</h3>
      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-hint">
            <p>你可以：</p>
            <ul>
              <li>提问：「这篇论文的核心创新是什么？」</li>
              <li>修改：「在实验章节后加一段局限性分析」</li>
            </ul>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-message ${m.role}`}>
            <div className="message-role">{m.role === "user" ? "你" : "Agent"}</div>
            <div
              className="message-content"
              dangerouslySetInnerHTML={{
                __html: md.render(m.content || (loading && i === messages.length - 1 ? "▊" : "")),
              }}
            />
          </div>
        ))}
        <div ref={endRef} />
      </div>
      <form className="chat-input-form" onSubmit={sendMessage}>
        <textarea
          placeholder="输入问题或修改指令..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              sendMessage();
            }
          }}
          rows={3}
          disabled={loading}
        />
        <button type="submit" disabled={loading || !input.trim()}>
          {loading ? "..." : "发送"}
        </button>
      </form>
    </div>
  );
}
