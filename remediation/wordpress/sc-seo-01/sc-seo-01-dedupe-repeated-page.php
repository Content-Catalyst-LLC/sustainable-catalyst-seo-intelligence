<?php
/**
 * SC-SEO-01 — restore a single copy of a page whose stored content was saved three times.
 *
 *   wp eval-file sc-seo-01-dedupe-repeated-page.php plan     <id>:<audited_md5> [...]
 *   wp eval-file sc-seo-01-dedupe-repeated-page.php apply    <backup-out.json> <id>:<audited_md5> [...]
 *   wp eval-file sc-seo-01-dedupe-repeated-page.php rollback <backup.json>
 *
 * Safety: acts only if (a) the post is a published page, (b) its current content still has the
 * audited MD5, (c) the content is exactly W+X+W+X+W+X' where W is the wrapper before the first
 * <h1> and the cut at the second W equals W + (text after the third <h1>), i.e. a verbatim
 * repeat. New content = content up to the second W. Saved via wp_update_post (revision kept).
 */
if (!defined('WP_CLI')) { exit(1); }
$mode = $args[0] ?? 'plan';
// Content written here is derived from content already stored by an administrator. WP-CLI runs
// without a user, so kses would otherwise strip markup (style blocks, attributes) on save.
kses_remove_filters();

if ($mode === 'rollback') {
    $b = (isset($args[1]) && is_readable($args[1])) ? json_decode(file_get_contents($args[1]), true) : null;
    if (!$b || empty($b['posts'])) { WP_CLI::error('cannot read backup'); }
    foreach ($b['posts'] as $row) {
        if (md5(get_post_field('post_content', $row['id'], 'raw')) !== $row['new_md5']) {
            WP_CLI::warning("post {$row['id']} changed since apply; skipped (use revisions)"); continue;
        }
        $r = wp_update_post(array('ID' => $row['id'], 'post_content' => wp_slash($row['old_content'])), true);
        WP_CLI::log(is_wp_error($r) ? "FAILED {$row['id']}" : "restored {$row['id']}");
    }
    do_action('epc_purge');
    WP_CLI::success('rollback complete');
    return;
}

$targets = array_slice($args, $mode === 'apply' ? 2 : 1);
$plan = array();
foreach ($targets as $t) {
    list($id, $md5) = array_pad(explode(':', $t, 2), 2, '');
    $id = (int) $id; $p = get_post($id);
    if (!$p || $p->post_type !== 'page' || $p->post_status !== 'publish') { WP_CLI::error("$id is not a published page"); }
    $c = $p->post_content;
    if (md5($c) !== $md5) { WP_CLI::error("$id content changed since audit (md5 " . md5($c) . ")"); }
    preg_match_all('/<h1\b/i', $c, $m, PREG_OFFSET_CAPTURE);
    if (count($m[0]) !== 3) { WP_CLI::error("$id: expected 3 <h1>, found " . count($m[0])); }
    $h = array_map(function ($x) { return $x[1]; }, $m[0]);
    $w = substr($c, 0, $h[0]);
    $occ = array(); $pos = 0;
    while (false !== ($pos = strpos($c, $w, $pos))) { $occ[] = $pos; $pos += strlen($w); }
    if (count($occ) !== 3) { WP_CLI::error("$id: wrapper occurs " . count($occ) . ' times'); }
    $new = substr($c, 0, $occ[1]);
    if (rtrim($new) !== rtrim($w . substr($c, $h[2]))) { WP_CLI::error("$id: copies are not verbatim repeats; refusing"); }
    $plan[] = array('id' => $id, 'url' => get_permalink($id), 'old' => $c, 'new' => $new);
    WP_CLI::log(sprintf('CHANGE %d %s  %d -> %d bytes, h1 3 -> %d', $id, get_permalink($id), strlen($c), strlen($new), preg_match_all('/<h1\b/i', $new)));
}
if ($mode !== 'apply') { WP_CLI::success(count($plan) . ' pages would change (no writes)'); return; }
$out = $args[1] ?? '';
if (!$out || file_exists($out)) { WP_CLI::error('backup path required and must not exist'); }
$backup = array('created_gmt' => gmdate('c'), 'site' => home_url('/'), 'posts' => array());
foreach ($plan as $r) { $backup['posts'][] = array('id' => $r['id'], 'url' => $r['url'], 'old_content' => $r['old'], 'old_md5' => md5($r['old']), 'new_md5' => md5($r['new'])); }
if (false === file_put_contents($out, json_encode($backup, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE))) { WP_CLI::error('cannot write backup'); }
foreach ($plan as $r) {
    $res = wp_update_post(array('ID' => $r['id'], 'post_content' => wp_slash($r['new'])), true);
    if (is_wp_error($res) || md5(get_post_field('post_content', $r['id'], 'raw')) !== md5($r['new'])) { WP_CLI::warning("post {$r['id']}: stored content differs from plan; roll back"); }
}
do_action('epc_purge');
WP_CLI::success(count($plan) . ' pages updated; backup at ' . $out);
