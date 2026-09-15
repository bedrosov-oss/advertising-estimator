---
name: tavily-cli
description: |
  Set up, authenticate, update, troubleshoot, or choose between Tavily CLI web commands. Use when the user asks about the Tavily CLI, installing Tavily skills, first-time setup, authentication, keyless limits, CLI updates, or which Tavily command to use. For an ordinary web task, use the specific search, extract, map, crawl, research, or dynamic-search skill instead.
---

# Tavily CLI

Web search, content extraction, site crawling, URL discovery, and deep research. Returns JSON optimized for LLM consumption.

Requires `tavily-cli`. Search and extract support capped keyless access; map,
crawl, and research require authentication.

Run `tvly --help` or `tvly <command> --help` for full option details.

## Setup

Эта версия адаптирована для комплекта сметчика из исходников Tavily под лицензией MIT; исходная лицензия находится в LICENSE.txt. Не читать личную память, не записывать новые воспоминания и не менять настройки аккаунта. Установка скилла не отключает техническое хранение данных сервисом.

Treat this as an optional dependency guide. A request to search public prices does not itself request installation, login, skill installation, or account changes. Use an available native search/page reader if `tvly` is missing.

When the user explicitly requests Tavily setup, first inspect the current executable and its help. Confirm the actual CLI commands and authentication flow from the current official documentation: https://github.com/tavily-ai/skills . Do not install a second copy or overwrite an existing configuration unnecessarily.

For ordinary searches, an available CLI can run `tvly search` and `tvly extract` in capped keyless mode according to the upstream documentation. Actual service responses determine whether this is available. If access is capped, switch to a native tool or explain the required access. Do not initiate login automatically.

The upstream `tvly init` command can install or update skills in detected agents; it is not a read-only check and is outside a download-only request. Do not run it unless the user has requested that setup scope. `tvly --help` and subcommand help are suitable read-only checks.

If authentication is expressly requested, use the supported secure sign-in flow of the host environment. Keep API keys out of commands, output files and chat; use only an approved credential mechanism. Do not bypass service restrictions or access-control failures.

## Workflow

Follow this escalation pattern — start simple, escalate when needed:

1. **Search** — No specific URL. Find pages, answer questions, discover sources.
2. **Extract** — Have a URL. Pull its content directly.
3. **Map** — Large site, need to find the right page. Discover URLs first.
4. **Crawl** — Need bulk content from an entire site section.
5. **Research** — Need comprehensive, multi-source analysis with citations.

| Need | Command | When |
|------|---------|------|
| Find pages on a topic | `tvly search` | No specific URL yet |
| Get a page's content | `tvly extract` | Have a URL |
| Find URLs within a site | `tvly map` | Need to locate a specific subpage |
| Bulk extract a site section | `tvly crawl` | Need many pages (e.g., all /docs/) |
| Deep research with citations | `tvly research` | Need multi-source synthesis |

For detailed command reference, use the individual skill for each command (e.g., `tavily-search`, `tavily-crawl`) or run `tvly <command> --help`.

Run `tvly` without a subcommand for the interactive REPL.

## Output

Search, extract, crawl, map, and research support `--json` for structured output.
Result-producing commands support `-o` to save the JSON response; crawl also
supports `--output-dir` for one Markdown file per page. Setup, authentication,
status, and update commands expose `--json` where documented but do not support
`-o`.

```bash
tvly search "react hooks" --json -o results.json
tvly extract "https://example.com/docs" -o docs.json
tvly crawl "https://docs.example.com" --output-dir ./docs/
```

## Tips

- **Always quote URLs** — shell interprets `?` and `&` as special characters.
- **Use `--json` for agentic workflows** when the selected command exposes it.
- **Read from stdin with `-`** — `echo "query" | tvly search -`
- **Exit codes**: 0 = success, 1 = setup/update failure, 2 = bad input, 3 = auth error, 4 = API or live-verification error.
