<?php
/**
 * SC-SEO-01 — remove a Knowledge Base article body <h1> that repeats the post title.
 * The single-article template already renders the title as the page H1.
 *
 *   wp eval-file sc-seo-01-kb-h1.php plan
 *   wp eval-file sc-seo-01-kb-h1.php apply    <backup-out.json>
 *   wp eval-file sc-seo-01-kb-h1.php rollback <backup.json>
 *
 * Scope: published sc_support_article posts only. Only an <h1>…</h1> whose plain text equals
 * the post title (case-, whitespace-, entity- and dash-insensitive) is removed; any other <h1>
 * is reported and left untouched. Saves via wp_update_post (WordPress records a revision).
 */
if (!defined('WP_CLI')) { exit(1); }

$mode = $args[0] ?? 'plan';
// Content written here is derived from content already stored by an administrator. WP-CLI runs
// without a user, so kses would otherwise strip markup (style blocks, attributes) on save.
kses_remove_filters();

function scseo_h1_norm($s) {
    $s = html_entity_decode(wp_strip_all_tags((string) $s), ENT_QUOTES | ENT_HTML5, 'UTF-8');
    $s = str_replace(array("\u{2014}", "\u{2013}", '&'), array('-', '-', 'and'), $s);
    $s = preg_replace('/\s+/u', ' ', $s);
    return mb_strtolower(trim($s));
}

if ($mode === 'rollback') {
    $b = (isset($args[1]) && is_readable($args[1])) ? json_decode(file_get_contents($args[1]), true) : null;
    if (!$b || empty($b['posts'])) { WP_CLI::error('cannot read backup'); }
    foreach ($b['posts'] as $row) {
        $cur = get_post_field('post_content', $row['id'], 'raw');
        if (md5($cur) !== $row['new_md5']) {
            WP_CLI::warning("post {$row['id']} changed since apply; skipping (restore manually from revision)");
            continue;
        }
        $r = wp_update_post(array('ID' => $row['id'], 'post_content' => wp_slash($row['old_content'])), true);
        WP_CLI::log(is_wp_error($r) ? "FAILED {$row['id']}: " . $r->get_error_message() : "restored {$row['id']}");
    }
    do_action('epc_purge');
    WP_CLI::success('rollback complete');
    return;
}

$posts = get_posts(array('post_type' => 'sc_support_article', 'post_status' => 'publish', 'numberposts' => -1));
$plan = array();
foreach ($posts as $p) {
    $c = $p->post_content;
    if (!preg_match_all('/<h1\b[^>]*>(.*?)<\/h1>\s*/is', $c, $m, PREG_SET_ORDER | PREG_OFFSET_CAPTURE)) {
        continue;
    }
    $title = scseo_h1_norm($p->post_title);
    $new = $c; $removed = 0; $kept = array();
    foreach (array_reverse($m) as $hit) {
        if (scseo_h1_norm($hit[1][0]) === $title) {
            $new = substr_replace($new, '', $hit[0][1], strlen($hit[0][0]));
            $removed++;
        } else {
            $kept[] = trim(wp_strip_all_tags($hit[1][0]));
        }
    }
    $plan[] = array('id' => $p->ID, 'url' => get_permalink($p), 'removed' => $removed, 'kept_other_h1' => $kept, 'old' => $c, 'new' => $new);
    WP_CLI::log(sprintf('%s %d %s  removed=%d other_h1_kept=%s', $removed ? 'CHANGE' : 'skip  ', $p->ID, get_permalink($p), $removed, json_encode($kept, JSON_UNESCAPED_UNICODE)));
}
$changes = array_values(array_filter($plan, function ($r) { return $r['removed'] > 0; }));
if ($mode !== 'apply') { WP_CLI::success(count($changes) . ' posts would change (no writes)'); return; }

$out = $args[1] ?? '';
if (!$out || file_exists($out)) { WP_CLI::error('backup path required and must not exist'); }
$backup = array('created_gmt' => gmdate('c'), 'site' => home_url('/'), 'posts' => array());
foreach ($changes as $r) {
    $backup['posts'][] = array('id' => $r['id'], 'url' => $r['url'], 'old_content' => $r['old'], 'new_md5' => md5($r['new']));
}
if (false === file_put_contents($out, json_encode($backup, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE))) { WP_CLI::error('cannot write backup'); }
foreach ($changes as $r) {
    $res = wp_update_post(array('ID' => $r['id'], 'post_content' => wp_slash($r['new'])), true);
    if (is_wp_error($res)) { WP_CLI::warning("FAILED {$r['id']}: " . $res->get_error_message()); continue; }
    if (md5(get_post_field('post_content', $r['id'], 'raw')) !== md5($r['new'])) {
        WP_CLI::warning("post {$r['id']}: stored content differs from planned (content filters?) — check and roll back if needed");
    }
}
do_action('epc_purge');
WP_CLI::success(count($changes) . ' posts updated; backup at ' . $out);
