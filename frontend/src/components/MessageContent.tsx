import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Safe markdown presentation; raw HTML remains escaped by react-markdown. */
export default function MessageContent({ text }: { text: string }) {
  const safe = typeof text === "string" ? text : String(text ?? "");
  if (!safe.trim()) return null;
  return (
    <div className="message-prose">
      <Markdown remarkPlugins={[remarkGfm]}>{safe}</Markdown>
    </div>
  );
}
