# Structured Telegram Rich Articles

Use this route for a real Telegram rich message, not for an ordinary message with `MessageEntity` ranges. The runtime module is `modules/rich_article.py`; it sends Telethon's raw `rich_message` field as `InputRichMessageHTML` or `InputRichMessageMarkdown`.

Read this reference before drafting or publishing an article with headings, paragraphs, labelled source links, quotations, lists, tables, expandable details, formulas, anchors, or media blocks.

## Choose the correct route

| Need | Route |
| --- | --- |
| Bold, code, a simple link, or a small quote inside an ordinary existing message | `richtext.py` with Telegram HTML entities |
| A new structured article with headings, paragraphs, block quotations and embedded local files | `rich_article.py` with Rich HTML or Rich Markdown |
| Edit an already published rich article | Extend the article module only after checking current Telethon and Telegram API support; do not force it through `richtext.py` |

`rich_article.py` is channel-only by design. It defaults to a dry run, verifies that the target is a broadcast channel, checks a bounded recent window for a matching visible title, and on execution checks that Telegram returned a non-empty `rich_message` with the requested title. Local media is uploaded with `messages.uploadMedia` and attached to the same `InputRichMessageHTML` or `InputRichMessageMarkdown` through its `files` field; it is never sent as a neighbouring Telegram post.

## Article composition

Prefer Rich HTML for generated articles because block boundaries are explicit. Use this stable shape unless the material needs a different one:

```html
<h1>Короткий заголовок с честным крючком</h1>
<p>Лид с главным фактом и без пересказа анонса.</p>
<h2>Что изменилось</h2>
<p>Один самостоятельный абзац.</p>
<blockquote>Короткая цитата или важная оговорка<cite>Источник</cite></blockquote>
<details>
  <summary>Источники</summary>
  <ul>
    <li><a href="https://example.com">Официальный анонс</a></li>
    <li><a href="https://example.com/docs">Документация</a></li>
  </ul>
</details>
```

## Embedded local media

For a local image, video, audio track, or document, put a Rich media block in the article and pass one matching `--media ID:KIND:PATH` value. The `ID` is a local binding key, not a Telegram file ID. It is 1-64 characters of `A-Z`, `a-z`, `0-9`, `_` or `-`.

```html
<h1>Заголовок</h1>
<p>Короткий лид.</p>
<img src="tg://photo?id=cover">
<audio src="tg://audio?id=interview"></audio>
<video src="tg://video?id=demo"></video>
<tg-document src="tg://document?id=report"></tg-document>
```

```bash
venv/bin/python scripts/userbotrun.py --account main modules/rich_article.py \
  --chat '<channel>' --title '<title>' --file '<article.html>' --format html \
  --media 'cover:photo:/absolute/path/cover.png' \
  --media 'interview:audio:/absolute/path/interview.m4a' \
  --media 'demo:video:/absolute/path/demo.mp4' \
  --media 'report:document:/absolute/path/report.pdf'
```

The module records file size, MIME type and SHA-256 in the dry-run. It rejects unknown links, an ID/kind mismatch, duplicate IDs, missing files, or a declared file that does not appear in the article. Photo, video and audio kinds must match the local MIME type; a document may use any ordinary file type. At execution it uploads the exact local files named in the reviewed command to the target chat without posting them, passes the resulting `InputRichFilePhoto` or `InputRichFileDocument` bindings in one rich-message request, then re-reads the sent message and checks that each expected attachment occurs in `rich_message.photos` or `rich_message.documents`.

### Media captions and credits

If the owner asks only to attach media, use the bare media block. Do not invent `<figure>`, `<figcaption>`, `<cite>`, image labels, photo credits, channel names or brand names. Generic text such as “Обложка к новости”, “Фото”, “BitFlip” or “Источник: канал” is forbidden as automatic metadata.

Add a caption or credit only when the owner explicitly asks for one. Then it must identify a real, news-relevant detail in the visual or name a supplied/verifiable source. Keep it concise and factual; never use a caption as filler or a place for unrequested author commentary.

Use one `<h1>` for the article title. Build it only after reading the complete fact packet and full article, not from the lead or one striking feature. First identify the event, mechanism, reader consequence, constraint, and every related fact that makes the item newsworthy, such as a launch plus a prize challenge or deadline. Then choose a concise factual hook that represents the editorial promise of the whole article. A title may combine two central facts when the connection is the story; it must not collapse them into a misleading metaphor such as claiming a product “started clicking buttons” when the actual change is a new structured site-tool route. If independent stories cannot fit honestly in one H1, narrow or split the article. Avoid neutral table-of-contents labels such as `X и Y: как это работает`. Follow the H1 with `<p>` blocks. Add `<h2>` or lower headings only when they divide genuinely separate material. When an article has reader-facing external sources, use exactly one final `<details><summary>Источники</summary>…</details>` block with labelled links. Do not generate an open `Ссылки` section or a service footer like `Источник проверен: <date>` unless the owner explicitly asks for that metadata. A short title, lead, two or three paragraphs, one quotation if it carries information, and collapsible labelled sources are usually enough for a news post.

## Rich HTML surface

The module passes documented Rich HTML through to Telegram. Its local parser rejects unknown or unbalanced tags; Telegram validates context-specific attributes on execution.

- Inline: `<b>`/`<strong>`, `<i>`/`<em>`, `<u>`/`<ins>`, `<s>`/`<strike>`/`<del>`, `<code>`, `<mark>`, `<sub>`, `<sup>`, `<tg-spoiler>`.
- Links and references: `<a href="https://…">`, `<a name="section-id"></a>`, `<a href="#section-id">`, `<tg-reference name="note-id">…</tg-reference>`, `<tg-emoji>`, `<tg-time>`, `<tg-math>`.
- Document blocks: `<h1>` through `<h6>`, `<p>`, `<pre>`, `<pre><code class="language-python">`, `<footer>`, `<hr/>`.
- Structure: `<ul>`, `<ol>`, `<li>`, checkbox `<input type="checkbox">`, `<blockquote>`, `<blockquote expandable>`, `<aside>`, `<details>` and `<summary>`.
- Advanced blocks: `<table>`, `<tg-math-block>`, media `<img>`/`<video>`/`<audio>`/`<tg-document>`, `<figure>`/`<figcaption>`, `<tg-collage>`, `<tg-slideshow>`, `<tg-map>`, and Rich Message buttons.

The module supports local media bindings for `<img>`, `<video>`, `<audio>` and `<tg-document>` as described above. Do not insert a remote URL merely to decorate an article. Buttons, maps and raw `blocks` remain outside this guarded publishing route and need their own target-specific update and tests.

## Limits and writing discipline

Telegram currently limits a rich message to 32,768 text characters, 500 blocks, 16 nesting levels, 50 media attachments and 20 table columns. Do not approach those limits accidentally. Tables are for comparison data, not regular prose. Details are for genuinely optional long material. Expandable quotations are for a long verbatim source passage, not a hidden disclaimer.

Use labelled hyperlinks, for example `<a href="https://openai.com/webmcp-challenge/">страница хакатона</a>`, instead of raw URLs in the body. Keep the source page and official documentation distinct. A source link does not prove a broader conclusion than the page actually supports.

## Publication procedure

1. Write the full Rich HTML or Rich Markdown file and extract one exact visible title.
2. Run `rich_article.py` without `--execute`. Inspect target type/title, source hash, format, duplicate result and planned rich constructor.
3. If there is local media, inspect every frozen `--media` entry and the matching `tg://…?id=…` source block; the dry-run must show the same IDs and kinds. If a caption or credit appears, confirm that the owner explicitly requested it and that it identifies real relevant information.
4. Obtain explicit owner authorization for the exact final article. A direct instruction to publish that article is authorization; a request to draft is not.
5. Run the identical command with `--execute`.
6. Accept success only when server read-back says: matching message ID, outgoing message, `has_rich_message=true`, positive block count, `title_present=true`, and every declared local attachment is present inside the returned rich message.

Official references:

- https://core.telegram.org/bots/api#rich-message-formatting-options
- https://core.telegram.org/type/RichText
