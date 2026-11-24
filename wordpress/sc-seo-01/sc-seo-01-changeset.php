<?php
/**
 * SC-SEO-01 settings/meta changeset — run with WP-CLI:
 *
 *   wp eval-file sc-seo-01-changeset.php plan     <changes.json>
 *   wp eval-file sc-seo-01-changeset.php lock     <changes.json>                      (prints a lock: md5 of every current value)
 *   wp eval-file sc-seo-01-changeset.php apply    <changes.json> <backup-out.json> [<lock.json>]
 *   wp eval-file sc-seo-01-changeset.php rollback <backup.json> [--force]
 *
 * With a lock, apply refuses unless every targeted value still has the md5 recorded at review
 * time, so a reviewed changeset can never overwrite a newer edit. Rollback likewise refuses a key
 * whose current value is no longer the value this changeset wrote (unless --force).
 * Rewrite rules are deliberately NOT flushed: a WP-CLI flush drops Rank Math's sitemap rules.
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
function scseo_h($v) { return md5(serialize($v)); }
function scseo_finish() {
    if (class_exists('\\RankMath\\Sitemap\\Cache')) { \RankMath\Sitemap\Cache::invalidate_storage(); }
    wp_cache_flush();
    do_action('epc_purge');
}

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
    $force = in_array('--force', $args, true);
    $W = $b['written'] ?? array('options' => array(), 'postmeta' => array());
    $drift = array();
    foreach ($b['options'] as $opt => $keys) {
        $cur = get_option($opt, array());
        foreach ($keys as $k => $old) {
            $now = array_key_exists($k, $cur) ? scseo_h($cur[$k]) : $ABSENT;
            if (isset($W['options'][$opt][$k]) && $W['options'][$opt][$k] !== $now) { $drift[] = "option $opt.$k"; }
        }
    }
    foreach ($b['postmeta'] as $row) {
        foreach ($row['meta'] as $k => $old) {
            $now = metadata_exists('post', $row['id'], $k) ? scseo_h(get_post_meta($row['id'], $k, true)) : $ABSENT;
            if (isset($W['postmeta'][$row['id']][$k]) && $W['postmeta'][$row['id']][$k] !== $now) { $drift[] = "post {$row['id']} $k"; }
        }
    }
    if ($drift && !$force) { WP_CLI::error('changed since apply, refusing (use --force to restore anyway): ' . implode(', ', $drift)); }
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
    scseo_finish();
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

$lock = array('options' => array(), 'postmeta' => array());
foreach (($c['options'] ?? array()) as $opt => $keys) {
    $cur = get_option($opt, array());
    foreach ($keys as $k => $new) { $lock['options'][$opt][$k] = array_key_exists($k, $cur) ? scseo_h($cur[$k]) : $ABSENT; }
}
foreach ($backup['postmeta'] as $b) {
    foreach ($b['meta'] as $k => $old) { $lock['postmeta'][$b['id']][$k] = ($old === $ABSENT) ? $ABSENT : scseo_h($old); }
}
if ($mode === 'lock') { echo json_encode($lock, JSON_PRETTY_PRINT) . "\n"; return; }

foreach ($plan as $p) {
    WP_CLI::log(($p['changes'] ? 'CHANGE ' : 'same   ') . $p['target'] . "\n    old: " . (is_scalar($p['old']) ? $p['old'] : json_encode($p['old'])) . "\n    new: " . (is_scalar($p['new']) ? $p['new'] : json_encode($p['new'])));
}
if ($mode !== 'apply') { WP_CLI::success(count($plan) . ' planned (no writes)'); return; }

$out = $args[2] ?? '';
if (!$out || file_exists($out)) { WP_CLI::error('backup path required and must not exist'); }
if (!empty($args[3])) {
    $want = json_decode((string) @file_get_contents($args[3]), true);
    if (!is_array($want)) { WP_CLI::error('cannot read lock'); }
    if ($want != $lock) {
        $diff = array();
        foreach ($lock['options'] as $o => $ks) { foreach ($ks as $k => $h) { if (($want['options'][$o][$k] ?? null) !== $h) { $diff[] = "option $o.$k"; } } }
        foreach ($lock['postmeta'] as $id => $ks) { foreach ($ks as $k => $h) { if (($want['postmeta'][$id][$k] ?? null) !== $h) { $diff[] = "post $id $k"; } } }
        WP_CLI::error('current values differ from the reviewed lock, refusing: ' . implode(', ', $diff ?: array('target set differs')));
    }
} elseif (empty($c['allow_unlocked'])) {
    WP_CLI::error('apply requires a lock file (run lock at review time)');
}
$backup['written'] = array('options' => array(), 'postmeta' => array());
foreach (($c['options'] ?? array()) as $opt => $keys) { foreach ($keys as $k => $new) { $backup['written']['options'][$opt][$k] = scseo_h($new); } }
foreach ($backup['postmeta'] as $i => $b) { foreach ($c['postmeta'][$i]['meta'] as $k => $new) { $backup['written']['postmeta'][$b['id']][$k] = scseo_h($new); } }
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
// Rank Math caches sitemaps; invalidate them, the object cache and the page cache. No rewrite flush.
scseo_finish();
WP_CLI::success('applied; backup at ' . $out);
