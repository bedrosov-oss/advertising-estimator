---
name: tavily-extract
description: |
  Extract clean markdown or text content from specific URLs via the Tavily CLI. Use this skill when the user has one or more URLs and wants their content, says "extract", "grab the content from", "pull the text from", "get the page at", "read this webpage", or needs clean text from web pages. Handles JavaScript-rendered pages, returns LLM-optimized markdown, and supports query-focused chunking for targeted extraction. Can process up to 20 URLs in a single call.
---

# tavily extract

Extract clean markdown or text content from one or more URLs.

## Before running

Эта версия адаптирована для комплекта сметчика из исходников Tavily под лицензией MIT; исходная лицензия находится в LICENSE.txt. Не читать личную память, не записывать новые воспоминания и не менять настройки аккаунта. Установка скилла не отключает техническое хранение данных сервисом.

Use the existing `tvly` command when available and appropriate for the user's task. Check `tvly extract --help` if flag support is uncertain. Search and extract have capped keyless access according to the upstream documentation; judge actual access from the response.

If `tvly` is unavailable, use an available native web search/page reading tool for the original task. Do not install software, run `tvly init`, authenticate, or change global settings merely to complete a public lookup. If no usable route exists, report the limitation and work with supplied files. Run setup or login only when the user has requested that action; see [tavily-cli setup](../tavily-cli/SKILL.md#setup).

If a keyless cap is reached, report it or switch to an available native tool. Do not initiate an unsolicited login. Never print API keys. A search snippet is not a verified price: open the exact supplier page and check the product, unit, quantity tier, tax and delivery terms.

## When to use

- You have a specific URL and want its content
- You need text from JavaScript-rendered pages
- Step 2 in the [workflow](../tavily-cli/SKILL.md): search → **extract** → map → crawl → research

## Quick start

```bash
# Single URL
tvly extract "https://example.com/article" --json

# Multiple URLs
tvly extract "https://example.com/page1" "https://example.com/page2" --json

# Query-focused extraction (returns relevant chunks only)
tvly extract "https://example.com/docs" --query "authentication API" --chunks-per-source 3 --json

# JS-heavy pages
tvly extract "https://app.example.com" --extract-depth advanced --json

# Save to file
tvly extract "https://example.com/article" -o article.json
```

## Options

| Option | Description |
|--------|-------------|
| `--query` | Rerank chunks by relevance to this query |
| `--chunks-per-source` | Chunks per URL (1-5, requires `--query`) |
| `--extract-depth` | `basic` (default) or `advanced` (for JS pages) |
| `--format` | `markdown` (default) or `text` |
| `--include-images` | Include image URLs |
| `--timeout` | Max wait time (1-60 seconds) |
| `-o, --output` | Save the JSON response to a file |
| `--json` | Structured JSON output |

## Extract depth

| Depth | When to use |
|-------|-------------|
| `basic` | Simple pages, fast — try this first |
| `advanced` | JS-rendered SPAs, dynamic content, tables |

## Tips

- **Max 20 URLs per request** — batch larger lists into multiple calls.
- **Use `--query` + `--chunks-per-source`** to get only relevant content instead of full pages.
- **Try `basic` first**, fall back to `advanced` if content is missing.
- **Set `--timeout`** for slow pages (up to 60s).
- **Inspect `failed_results` even after exit code 0.** A successful request can
  still return no extracted pages. Retry the affected URL with `advanced` when
  appropriate, otherwise report the per-URL failure instead of treating the
  request as complete.
- If search results already contain the content you need (via `--include-raw-content`), skip the extract step.

## See also

- [tavily-search](../tavily-search/SKILL.md) — find pages when you don't have a URL
- [tavily-crawl](https://github.com/tavily-ai/skills/blob/main/skills/tavily-crawl/SKILL.md) — extract content from many pages on a site
