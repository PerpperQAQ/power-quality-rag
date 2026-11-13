import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AnswerMarkdown } from "../output/full-qa/markdown-check/AnswerMarkdown.js";

const render = text => renderToStaticMarkup(createElement(AnswerMarkdown, { text }));
assert.match(render("**420 点**\n\n- 冷却 50 秒\n- 依据 [2]"), /<strong>420 点<\/strong>[\s\S]*<ul>[\s\S]*依据 \[2\]/);
assert.match(render("| 英雄 | 冷却 |\n| --- | --- |\n| 夜歌 | 50 秒 |"), /answer-table-scroll[\s\S]*<table>[\s\S]*<td>50 秒<\/td>/);
assert.match(render("```python\nprint('hello')\n```"), /<pre><code class="language-python">/);
assert.doesNotMatch(render("<script>alert(1)</script>\n\n安全正文"), /<script>/);
assert.doesNotMatch(render("[打开](javascript:alert%281%29)"), /href="javascript:/);
assert.doesNotMatch(render("![远程图](https://example.com/pixel.png)"), /<img/);
console.log("6 answer rendering checks passed: emphasis/list/citation, tables, code, raw HTML, URL filtering, image suppression");
