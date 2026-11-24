# SC-SEO-02 bounded public crawl

The collector uses a fixed authorization profile for Sustainable Catalyst. It permits the seven exact seed paths and at most 15 distinct paths under `/support/guides/`. It rejects queries, alternate origins, unapproved redirects and unsupported path encodings. Unknown outbound paths can be reported as unverified links without becoming requests. The only discovery exceptions are `/robots.txt` and one same-origin sitemap directly specified in the verified file. A sitemap index does not authorize retrieval of its children.

The collector invokes curl once per approved GET in a synchronous loop, disables proxy configuration, retries and redirect following, and waits at least three seconds after each request finishes. Redirect targets re-enter the same scope and robots checks. The collector caps page-path attempts as well as total requests, and reserves time for transport termination. A transport error, rate limit or access challenge terminates collection. Two consecutive 5xx responses also terminate the run. Response bodies are capped at 4 MiB. The source manifest records HTTP status, elapsed time, start and end offsets, response hashes and skip reasons.

The public capture is the authoritative HTTP evidence. SiteOne v2.5.1 analyzes a loopback-only projection of captured HTML metadata, heading levels and links between captured URLs. The projection replays observed statuses and captured in-scope redirect hops. Browser, JavaScript and external assets are out of scope. Canonicals, robots directives, page metadata, internal link context and timings are evaluated against the original capture. SiteOne results and the prior controlled loopback run are kept separate. Historical Search Console observations are optional offline inputs and never serve as current HTTP evidence or a quality assessment.

## Reproduction from a private capture

Run from the application checkout. `--reuse-siteone` preserves the original offline replay and regenerates the six review artifacts without contacting the public site. A repeat public crawl requires authorization.

```bash
PYTHONPATH=. python3 scripts/public_crawl_report.py \
  /private/capture /private/verified/siteone-crawler /private/loopback/acceptance-receipt.json \
  --gsc-dir /private/approved-gsc-inputs --reuse-siteone
python3 -m unittest discover -s tests -v
```

The initial analysis omits `--reuse-siteone` to construct the loopback projection. Raw HTTP bodies and headers, Search Console inputs, SiteOne output and operational reports belong in restricted storage. Only code and aggregate acceptance results belong in the public source repository.

## Sanitized acceptance · 2026-11-20

The single authorized run collected 22 HTML pages and two discovery files in 24 sequential GETs over 140.290 seconds. Every response was HTTP 200. The minimum measured inter-request gap was 3.000774 seconds. The 15-guide quota was reached. All outbound request paths passed the allowlist and robots checks. The pinned SiteOne analyzer processed all 22 captured pages through the offline projection. The original controlled fixture remains a separate run and continues to cover 301, 403 and 404 handling.

The public sample surfaced missing meta descriptions, H1 structure issues, and several response times above five seconds. No 3xx/4xx response was observed in the sampled pages, and destinations outside the capture remain unverified. The declared sitemap was an index, and per-URL membership remains open. The private reconciliation imported available historical Search Console workbooks as a separate source. Two report regenerations using the preserved capture and SiteOne output produced identical artifact hashes. The public repository contains this aggregate summary and reproducible code.
