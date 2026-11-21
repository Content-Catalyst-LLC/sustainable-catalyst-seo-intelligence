# Publications discoverability diagnostics

Read an authorized initial HTML capture for the Publications hub. Additional `--html-response URL FILE` inputs supply observed initial-HTML link edges for that capture set. Optional `--publication-rendered-html URL FILE` imports a saved post-initialization DOM separately. The dashboard and private JSON report keep ordinary anchors, embedded registry associations, rendered anchors, Google export classifications, and HTTP observations distinct.

```bash
python3 seo_studio.py --input-dir /private/gsc --output-dir /private/audit \
    --publication-hub-html /private/publications.body \
    --publication-rendered-html https://sustainablecatalyst.com/publications/ /private/publications-initial-dom.html \
    --imported-at 2026-11-20T18:05:00Z
```

An existing HTML evidence input identifies the status of a canonical only when that URL's document itself was captured. A mapped destination in JSON implies an editorial association. A graph reachability gap is bounded to observed pages. Supply authorized local sitemap leaves and WordPress records through the existing Studio options when available. The module does not execute page code or issue network requests.

For a controlled interaction replay, use the matching JavaScript extracted from the Library repository by commit and a local jsdom installation. Only the supplied, reviewed script executes. The saved document's scripts and external resources stay disabled. The fixture exercises local playback buttons without following links. Keep replay reports private.

```bash
git -C /private/library show ab0da6366174e1bb33f8177bb1ecce64e5f9d0c0:sustainable-catalyst-library/assets/js/sc-library-field-spotlights.js > /private/spotlights.js
node scripts/replay_publications.cjs /private/publications.body /private/spotlights.js /private/replay
python3 -m unittest discover -s tests -v
```

The Nov. 20 capture shows 14 fields, 170 map panels, and 179 distinct article targets in the embedded registry. The initial body links to 14 map destinations and 32 of the article targets. The captured link graph does not prove sitewide orphaning. Repeat this check with authorized archives and representative Article Map responses before using missing-path counts as a sitewide measure. The evidence supports a discoverability improvement on the hub. It does not establish why the historical Google indexed count fell.

## Compare two captures from the same staging hub

Create an initial discovery JSON from the baseline HTML. Run the same command against the proposed HTML and pass `--publication-baseline /private/baseline/seo-publication-discovery.json`. Both snapshots must use the same property and hub. The dashboard shows the link deltas and the private comparison JSON includes per-field coverage, exact map route changes, canonical and robots comparisons, and shortest observed capture-graph depth. A missing path stays unknown. Compare staging before and after as a pair. Treat the earlier production capture as a separate baseline with a different content cohort.
