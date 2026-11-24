<?php
/**
 * Plugin Name: SC-SEO-01 Search Hygiene
 * Description: Precise schema types, a Knowledge Base sitemap quality gate, and optional cacheable delivery of Customizer Additional CSS. No content, styling, robots or status changes.
 * Version: 1.0.0
 *
 * Features
 *  1.  Knowledge Base guides (sc_support_article) are typed TechArticle instead of
 *      Article/BlogPosting in Rank Math JSON-LD (no-op if Rank Math emits no article).
 *  1b. Pages drop Rank Math's default Article node (they are hubs/application pages);
 *      WebPage, WebSite, Organization and BreadcrumbList are kept.
 *  1c. KB sitemap quality gate: blocked or self-declared pending/draft guides are not
 *      listed in the sitemap; the redirecting KB archive is not listed. Only takes effect
 *      once the KB post type is enabled in Rank Math's sitemap settings.
 *  2.  Optional: serve the Customizer "Additional CSS" (wp-custom-css) as a versioned,
 *     browser-cacheable stylesheet at the SAME position in <head> instead of inlining
 *     ~1 MB into every HTML document. OFF unless the option sc_seo_01_external_custom_css = 1.
 *     Bytes are identical; falls back to the core inline output on any failure and in the
 *     Customizer preview.
 *
 * Rollback: delete this file (and, for feature 2, the uploads/sc-seo-01/ directory).
 */
if (!defined('ABSPATH')) { exit; }

/* 1. TechArticle for Knowledge Base guides ------------------------------------------- */
add_filter('rank_math/json_ld', function ($data) {
    if (!is_singular('sc_support_article') || !is_array($data)) {
        return $data;
    }
    foreach ($data as $key => $entity) {
        if (!is_array($entity) || empty($entity['@type'])) {
            continue;
        }
        $types = (array) $entity['@type'];
        if (array_intersect($types, array('Article', 'BlogPosting', 'NewsArticle'))) {
            $data[$key]['@type'] = 'TechArticle';
        }
    }
    return $data;
}, 99);

/* 1b. WordPress pages are hubs/application pages, not articles ----------------------- */
// Rank Math's page default is "Article"; turning the default off also drops the
// WebPage/WebSite/Organization graph, so remove only the Article node on pages.
add_filter('rank_math/json_ld', function ($data) {
    if (!is_page() || !is_array($data)) {
        return $data;
    }
    foreach ($data as $key => $entity) {
        if (is_array($entity) && !empty($entity['@type']) && array_intersect((array) $entity['@type'], array('Article', 'BlogPosting', 'NewsArticle'))) {
            unset($data[$key]);
        }
    }
    return $data;
}, 99);

/* 1c. Knowledge Base sitemap quality gate ------------------------------------------- */
// Only guides the site itself treats as usable are offered to search engines via the
// sitemap. A guide is held back while its integrity state is "blocked" or while its own
// published status line says it is pending, a draft, or not production-verified. When the
// editorial record changes, the guide enters the sitemap on the next sitemap rebuild.
// This never changes robots directives, content or status.
function sc_seo_01_kb_guide_is_sitemap_eligible($post) {
    $post = (object) $post;
    if ('blocked' === (string) get_post_meta($post->ID, '_scfs_integrity_state', true)) {
        return false;
    }
    $text = preg_replace('/\s+/u', ' ', wp_strip_all_tags(strip_shortcodes((string) $post->post_content)));
    if (preg_match('/Status:\s*([^.]{0,160})/iu', $text, $m)
        && preg_match('/pending|draft|not a production-verified|unverified|not yet/iu', $m[1])) {
        return false;
    }
    return true;
}

add_filter('rank_math/sitemap/entry', function ($url, $type, $post) {
    // Rank Math passes raw query rows (stdClass with ID, post_type, post_content, ...).
    if ('post' === $type && is_object($post) && isset($post->post_type, $post->ID) && 'sc_support_article' === $post->post_type
        && !sc_seo_01_kb_guide_is_sitemap_eligible($post)) {
        return false;
    }
    return $url;
}, 10, 3);

// The KB archive (/support-documentation/) 301-redirects to the Support Center, so it must
// not be listed in the KB sitemap; /support/ itself is listed in the page sitemap.
add_filter('rank_math/sitemap/post_type_archive_link', function ($link, $post_type) {
    return 'sc_support_article' === $post_type ? false : $link;
}, 10, 2);

/* 2. Cacheable Additional CSS (opt-in) ---------------------------------------------- */
function sc_seo_01_custom_css_url() {
    $css = wp_get_custom_css();
    if ('' === trim((string) $css)) {
        return '';
    }
    $hash = substr(md5($css), 0, 12);
    $up = wp_upload_dir(null, false);
    if (!empty($up['error'])) {
        return '';
    }
    $dir = trailingslashit($up['basedir']) . 'sc-seo-01';
    $file = $dir . '/custom-css-' . $hash . '.css';
    if (!file_exists($file)) {
        if (!wp_mkdir_p($dir)) {
            return '';
        }
        $tmp = $file . '.' . wp_generate_password(6, false) . '.tmp';
        // Same bytes core would inline (core runs strip_tags() on output).
        if (false === file_put_contents($tmp, strip_tags($css)) || !rename($tmp, $file)) {
            @unlink($tmp);
            return '';
        }
    }
    return set_url_scheme(trailingslashit($up['baseurl']) . 'sc-seo-01/custom-css-' . $hash . '.css');
}

add_action('wp_head', function () {
    if (!get_option('sc_seo_01_external_custom_css') || is_customize_preview()) {
        return;
    }
    if (false === has_action('wp_head', 'wp_custom_css_cb')) {
        return;
    }
    $url = sc_seo_01_custom_css_url();
    if ('' === $url) {
        return; // core inline output stays in place
    }
    remove_action('wp_head', 'wp_custom_css_cb', 101);
    add_action('wp_head', function () use ($url) {
        printf("<link rel=\"stylesheet\" id=\"wp-custom-css\" href=\"%s\" media=\"all\" />\n", esc_url($url));
    }, 101);
}, 0);
