# Structured Telegram Rich Articles

Use this route for a real Telegram rich message, not for an ordinary message with `MessageEntity` ranges. The runtime module is `modules/rich_article.py`; it sends Telethon's raw `rich_message` field as `InputRichMessageHTML` or `InputRichMessageMarkdown`.

Read this reference before drafting or publishing an article with headings, paragraphs, labelled source links, quotations, lists, tables, expandable details, formulas, anchors, or media blocks.

## Choose the correct route

| Need | Route |
| --- | --- |
| Bold, code, a simple link, or a small quote inside an ordinary existing message | `richtext.py` with Telegram HTML entities |
| A new structured article with headings, paragraphs, block quotations and document links | `rich_article.py` with Rich HTML or Rich Markdown |
| Edit an already published rich article | Extend the article module only after checking current Telethon and Telegram API support; do not force it through `richtext.py` |

`rich_article.py` is channel-only by design. It defaults to a dry run, verifies that the target is a broadcast channel, checks a bounded recent window for a matching visible title, and on execution checks that Telegram returned a non-empty `rich_message` with the requested title.

## Article composition

Prefer Rich HTML for generated articles because block boundaries are explicit. Use this stable shape unless the material needs a different one:

```html
<h1>Точный заголовок</h1>
<p>Лид с главным фактом и без пересказа анонса.</p>
<h2>Что изменилось</h2>
<p>Один самостоятельный абзац.</p>
<blockquote>Короткая цитата или важная оговорка<cite>Источник</cite></blockquote>
<h2>Ссылки</h2>
<ul>
  <li><a href="https://example.com">Официальный анонс</a></li>
  <li><a href="https://example.com/docs">Документация</a></li>
</ul>
<footer>Источник проверен: 2026-08-26</footer>
```

Use one `<h1>` for the article title. Follow it with `<p>` blocks. Add `<h2>` or lower headings only when they divide genuinely separate material. A short title, lead, two or three paragraphs, one quotation if it carries information, and labelled sources are usually enough for a news post.

## Rich HTML surface

The module passes documented Rich HTML through to Telegram. Its local parser rejects unknown or unbalanced tags; Telegram validates context-specific attributes on execution.

- Inline: `<b>`/`<strong>`, `<i>`/`<em>`, `<u>`/`<ins>`, `<s>`/`<strike>`/`<del>`, `<code>`, `<mark>`, `<sub>`, `<sup>`, `<tg-spoiler>`.
- Links and references: `<a href="https://…">`, `<a name="section-id"></a>`, `<a href="#section-id">`, `<tg-reference name="note-id">…</tg-reference>`, `<tg-emoji>`, `<tg-time>`, `<tg-math>`.
- Document blocks: `<h1>` through `<h6>`, `<p>`, `<pre>`, `<pre><code class="language-python">`, `<footer>`, `<hr/>`.
- Structure: `<ul>`, `<ol>`, `<li>`, checkbox `<input type="checkbox">`, `<blockquote>`, `<blockquote expandable>`, `<aside>`, `<details>` and `<summary>`.
- Advanced blocks: `<table>`, `<tg-math-block>`, media `<img>`/`<video>`/`<audio>`/`<tg-document>`, `<figure>`/`<figcaption>`, `<tg-collage>`, `<tg-slideshow>`, `<tg-map>`, and Rich Message buttons.

Media, buttons, maps and raw `blocks` require extra target-specific authority and richer verification. Do not insert a remote URL merely to decorate an article. For the text-only article module, prefer headings, paragraphs, quotations, lists, labelled links, anchors and details. Extend media or button handling as a separate module update with its own tests and explicit owner request.

## Limits and writing discipline

Telegram currently limits a rich message to 32,768 text characters, 500 blocks, 16 nesting levels, 50 media attachments and 20 table columns. Do not approach those limits accidentally. Tables are for comparison data, not regular prose. Details are for genuinely optional long material. Expandable quotations are for a long verbatim source passage, not a hidden disclaimer.

Use labelled hyperlinks, for example `<a href="https://openai.com/webmcp-challenge/">страница хакатона</a>`, instead of raw URLs in the body. Keep the source page and official documentation distinct. A source link does not prove a broader conclusion than the page actually supports.

## Publication procedure

1. Write the full Rich HTML or Rich Markdown file and extract one exact visible title.
2. Run `rich_article.py` without `--execute`. Inspect target type/title, source hash, format, duplicate result and planned rich constructor.
3. Obtain explicit owner authorization for the exact final article. A direct instruction to publish that article is authorization; a request to draft is not.
4. Run the identical command with `--execute`.
5. Accept success only when server read-back says: matching message ID, outgoing message, `has_rich_message=true`, positive block count, and `title_present=true`.

Official references:

- https://core.telegram.org/bots/api#rich-message-formatting-options
- https://core.telegram.org/type/RichText
