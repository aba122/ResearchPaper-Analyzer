import { useEffect, useRef } from "react";

export default function ProgressPanel({ messages, done }) {
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="progress-panel">
      <h2>{done ? "分析完成" : "分析进度"}</h2>
      <div className="progress-log">
        {messages.map((msg, i) => (
          <div key={i} className="progress-entry">
            <span className="progress-icon">{done && i === messages.length - 1 ? "✅" : "⏳"}</span>
            <span className="progress-text">{msg}</span>
          </div>
        ))}
        {!done && <div className="progress-spinner">分析中...</div>}
        <div ref={endRef} />
      </div>
    </div>
  );
}
