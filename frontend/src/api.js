/**
 * 封装所有后端 API 调用
 */

const BASE = "/api";

export async function searchPapers(query) {
  const res = await fetch(`${BASE}/search?q=${encodeURIComponent(query)}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function resolvePaper(urlOrId) {
  const res = await fetch(`${BASE}/resolve?url=${encodeURIComponent(urlOrId)}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listPapers() {
  const res = await fetch(`${BASE}/papers`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getPaper(arxivId) {
  const res = await fetch(`${BASE}/paper/${arxivId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/**
 * POST /api/analyze — returns EventSource-compatible SSE stream
 * onMessage(event: {type, message|content|arxiv_id}) called for each event
 */
export function startAnalysis(payload, onMessage, onDone, onError) {
  fetch(`${BASE}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
    .then(async (res) => {
      if (!res.ok) {
        const text = await res.text();
        onError(text);
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const event = JSON.parse(line.slice(6));
              if (event.type === "done") {
                onDone(event);
              } else {
                onMessage(event);
              }
            } catch {}
          }
        }
      }
    })
    .catch(onError);
}

/**
 * POST /api/paper/{id}/chat — SSE stream
 */
export function chatWithAgent(arxivId, message, history, onChunk, onDone, onError) {
  fetch(`${BASE}/paper/${arxivId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history }),
  })
    .then(async (res) => {
      if (!res.ok) {
        const text = await res.text();
        onError(text);
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const event = JSON.parse(line.slice(6));
              if (event.type === "done") {
                onDone();
              } else if (event.type === "text") {
                onChunk(event.content);
              } else if (event.type === "tool_use") {
                onChunk(`\n[调用工具: ${event.tool}]\n`);
              }
            } catch {}
          }
        }
      }
    })
    .catch(onError);
}

export function getImageUrl(arxivId, filename) {
  return `${BASE}/paper/${arxivId}/images/${filename}`;
}

export async function getTags() {
  const res = await fetch(`${BASE}/tags`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.tags || [];
}

export function startCodeAnalysis(arxivId, payload, onMessage, onDone, onError) {
  fetch(`${BASE}/paper/${arxivId}/analyze-code`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
    .then(async (res) => {
      if (!res.ok) {
        const text = await res.text();
        onError(text);
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const event = JSON.parse(line.slice(6));
              if (event.type === "code_done") {
                onDone(event);
              } else if (event.type === "error") {
                onError(event.message);
              } else {
                onMessage(event);
              }
            } catch {}
          }
        }
      }
    })
    .catch(onError);
}

export async function getCodeAnalysis(arxivId) {
  const res = await fetch(`${BASE}/paper/${arxivId}/code-analysis`);
  if (!res.ok) return null;
  return (await res.json()).content;
}
