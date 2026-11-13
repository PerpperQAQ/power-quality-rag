import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function AnswerMarkdown({ text }: { text: string }) {
  return (
    <Markdown
      remarkPlugins={[remarkGfm]}
      skipHtml
      disallowedElements={["img"]}
      components={{
        table: ({ children }) => (
          <div className="answer-table-scroll"><table>{children}</table></div>
        ),
        a: ({ href, children }) => (
          <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
        ),
      }}
    >
      {text}
    </Markdown>
  );
}
