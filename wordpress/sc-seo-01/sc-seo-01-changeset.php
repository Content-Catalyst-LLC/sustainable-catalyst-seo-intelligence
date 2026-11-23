<?php
/**
 * SC-SEO-01 settings/meta changeset — run with WP-CLI:
 *
 *   wp eval-file sc-seo-01-changeset.php plan   <changes.json>
 *   wp eval-file sc-seo-01-changeset.php apply  <changes.json> <backup-out.json>
 *   wp eval-file sc-seo-01-changeset.php rollback <backup.json>
 *
 * changes.json:
 * {
 *   "options": { "<option_name>": { "<key>": <value>, ... } },     // merged into array options
 *   "postmeta": [ { "match": {"path": "/institution/"} | {"id": 123} | {"front_page": true},
 *                   "meta": { "rank_math_description": "..." } } ]
 * }
 *
 * Every prior value (including "absent") is written to the backup file BEFORE any write.
 * Only keys named in changes.json are touched. No content, status or slug is ever changed.
 */
if (!defined('WP_CLI')) { exit(1); }

$mode = $args[0] ?? 'plan';
$ABSENT = '__SC_SEO_01_ABSENT__';

function scseo_resolve($m) {
    if (!empty($m['front_page'])) { return (int) get_option('page_on_front'); }
    if (!empty($m['id'])) { return (int) $m['id']; }
    if (!empty($m['path'])) {
        $id = url_to_postid(home_url($m['path']));
        if (!$id) {
            $p = get_page_by_path(trim($m['path'], '/'), OBJECT, array('page', 'post', 'sc_support_article', 'sc_foundation_doc'));
            $id = $p ? $p->ID : 0;
        }
        return (int) $id;
    }
    return 0;
}

if ($mode === 'rollback') {
    $b = (isset($args[1]) && is_readable($args[1])) ? json_decode(file_get_contents($args[1]), true) : null;
    if (!$b) { WP_CLI::error('cannot read backup'); }
    foreach ($b['options'] as $opt => $keys) {
        $cur = get_option($opt, array());
        foreach ($keys as $k => $old) {
            if ($old === $ABSENT) { unset($cur[$k]); } else { $cur[$k] = $old; }
        }
        update_option($opt, $cur);
        WP_CLI::log("restored option $opt (" . count($keys) . ' keys)');
    }
    foreach ($b['postmeta'] as $row) {
        foreach ($row['meta'] as $k => $old) {
            if ($old === $ABSENT) { delete_post_meta($row['id'], $k); } else { update_post_meta($row['id'], $k, $old); }
        }
        clean_post_cache($row['id']);
        WP_CLI::log("restored post {$row['id']}");
    }
    if (class_exists('\\RankMath\\Sitemap\\Cache')) { \RankMath\Sitemap\Cache::invalidate_storage(); }
    flush_rewrite_rules(false);
    do_action('epc_purge');
    WP_CLI::success('rollback complete');
    return;
}

$c = json_decode(file_get_contents($args[1]), true);
if (!$c) { WP_CLI::error('cannot read changes'); }
$backup = array('created_gmt' => gmdate('c'), 'site' => home_url('/'), 'options' => array(), 'postmeta' => array());
$plan = array();

foreach (($c['options'] ?? array()) as $opt => $keys) {
    $cur = get_option($opt, array());
    if (!is_array($cur)) { WP_CLI::error("option $opt is not an array"); }
    foreach ($keys as $k => $new) {
        $old = array_key_exists($k, $cur) ? $cur[$k] : $ABSENT;
        $backup['options'][$opt][$k] = $old;
        $plan[] = array('target' => "option $opt.$k", 'old' => $old, 'new' => $new, 'changes' => $old !== $new);
    }
}
foreach (($c['postmeta'] ?? array()) as $row) {
    $id = scseo_resolve($row['match']);
    if (!$id || !get_post($id)) { WP_CLI::error('unresolved post: ' . json_encode($row['match'])); }
    if (!empty($row['expect_id']) && (int) $row['expect_id'] !== $id && empty($c['ignore_expect_id'])) {
        WP_CLI::error("post id mismatch for " . json_encode($row['match']) . ": resolved $id, expected {$row['expect_id']}");
    }
    $post = get_post($id);
    if ($post->post_status !== 'publish') { WP_CLI::error("post $id is not published; refusing"); }
    $b = array('id' => $id, 'url' => get_permalink($id), 'meta' => array());
    foreach ($row['meta'] as $k => $new) {
        $old = metadata_exists('post', $id, $k) ? get_post_meta($id, $k, true) : $ABSENT;
        $b['meta'][$k] = $old;
        $plan[] = array('target' => "post $id " . get_permalink($id) . " $k", 'old' => $old, 'new' => $new, 'changes' => $old !== $new);
    }
    $backup['postmeta'][] = $b;
}

foreach ($plan as $p) {
    WP_CLI::log(($p['changes'] ? 'CHANGE ' : 'same   ') . $p['target'] . "\n    old: " . (is_scalar($p['old']) ? $p['old'] : json_encode($p['old'])) . "\n    new: " . (is_scalar($p['new']) ? $p['new'] : json_encode($p['new'])));
}
if ($mode !== 'apply') { WP_CLI::success(count($plan) . ' planned (no writes)'); return; }

$out = $args[2] ?? '';
if (!$out || file_exists($out)) { WP_CLI::error('backup path required and must not exist'); }
if (false === file_put_contents($out, json_encode($backup, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES))) { WP_CLI::error('cannot write backup'); }

foreach (($c['options'] ?? array()) as $opt => $keys) {
    $cur = get_option($opt, array());
    foreach ($keys as $k => $new) { $cur[$k] = $new; }
    update_option($opt, $cur);
}
foreach ($backup['postmeta'] as $i => $b) {
    foreach ($c['postmeta'][$i]['meta'] as $k => $new) { update_post_meta($b['id'], $k, $new); }
    clean_post_cache($b['id']);
}
// Rank Math caches sitemaps; flush them and the page cache.
if (class_exists('\\RankMath\\Sitemap\\Cache')) { \RankMath\Sitemap\Cache::invalidate_storage(); }
flush_rewrite_rules(false);
do_action('epc_purge');
WP_CLI::success('applied; backup at ' . $out);
