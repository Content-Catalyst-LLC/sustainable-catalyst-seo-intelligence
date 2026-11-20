# SC-SEO-02.1E offline sitemap and technical review

Run with the existing accepted capture and a private output directory. The importer reads no public URLs.

```bash
python3 scripts/reconcile_sitemaps.py /private/accepted-capture /private/new-review \
  --child-sitemaps /private/authorized-child-xml \
  --publication-status /private/wordpress-status.json
```

The child directory uses the original filenames listed by the captured sitemap index. The importer accepts UTF-8 sitemap leaf files without DTDs. Missing or rejected leaves leave unobserved membership unknown. Positive membership retains the declaring files and their digests. An optional WordPress export is an array of records with exact `url` and `status` strings. Public delivery and the workflow status have separate fields. Historical Search Console counts retain their own source category.

The timing check repeats transfer and HTML analysis for four saved response bodies over loopback. It also inventories inline style bytes across the captured guides. These measurements exclude public network transit, CDN/cache behavior and WordPress response generation.

The initial review verified one self-canonical on each of the 22 captured HTTP 200 pages. Five local timing repeats per slow guide completed. The private remediation review specifies four exact description drafts and two heading changes. The 12 child XML documents and an authoritative WordPress status export were pending for that review. The review made zero live origin requests.
