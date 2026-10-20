/**
 * Emoji-only icon catalog.
 *
 * fomo.nyc generates this file (js/core/eventIconCatalog.js) from ~300 bundled
 * SVG pictograms under config/event-icons. This deployment does not ship those
 * assets, so the catalog is deliberately empty and IconManager falls back to
 * rasterizing the record's own emoji instead of downloading artwork.
 *
 * The shape still matches the generated file so iconManager needs no knowledge
 * of which mode it is in: adding the artwork packs later means replacing this
 * file and restoring the IconCatalog.icons entries — no other change.
 *
 * @module IconCatalog
 */
const IconCatalog = {
    // 0 = no artwork bundled. IconManager treats this as "draw the emoji".
    schemaVersion: 0,
    revision: 'emoji-only',

    /** id -> { id, revision, url } artwork descriptors. Empty here. */
    icons: {},
    /** emoji -> icon id. Empty here, so every record resolves to its glyph. */
    emoji: {},
    /** pack id -> { url, gzipBytes } groups of artwork. Empty here. */
    packs: {},

    /** No artwork and no fallback artwork: the glyph is the only source. */
    fallbackId: null,
    fallbackUrl: null,
};