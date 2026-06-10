/* sprites.js — asset manifest + resolver.
 *
 * The viewer is fully playable with emoji/CSS fallbacks (no binary assets needed). When real
 * sprites are generated (Gemini "nano-banana" -> PNG -> background removed -> ui/assets/), fill in
 * the matching entry in MANIFEST below and it overrides the emoji automatically. Missing or
 * broken images fall back to emoji at render time (the <img> onerror swaps in the emoji span).
 *
 * Key conventions:
 *   station:<TYPE>          ING | BOARD | STOVE | PLATE | PASS | BIN
 *   chef / chef:carry       the cook (optionally a "carrying" pose)
 *   ing:<name>:<STATE>      e.g. ing:patty:COOKED   (per-state sprite, preferred)
 *   ing:<name>              e.g. ing:patty          (state-agnostic fallback; a CSS ring shows state)
 *   dish:<recipe>           e.g. dish:burger        (a plated, finished dish)
 *   floor / counter         tile backgrounds (optional)
 */

const KR = window.KR || (window.KR = {});

KR.sprites = (() => {
  const BASE = "assets/";

  // Filled at runtime from assets/manifest.json (written by generate_sprites.py); see the
  // key conventions in the header. Empty = pure emoji mode.
  const MANIFEST = {};

  // Emoji fallbacks — chosen so every entity reads clearly without any art pipeline.
  const EMOJI = {
    "station:ING": "📦", "station:BOARD": "🔪", "station:STOVE": "🍳",
    "station:PLATE": "🍽️", "station:PASS": "🛎️", "station:BIN": "🗑️",
    "chef": "🧑‍🍳", "chef:carry": "🧑‍🍳",
    "ing:bun": "🍞", "ing:patty": "🥩", "ing:lettuce": "🥬", "ing:tomato": "🍅",
    "ing:onion": "🧅", "ing:cheese": "🧀", "ing:broth_base": "🥫", "ing:mushroom": "🍄",
    "ing:noodles": "🍜", "ing:egg": "🥚",
    "dish:burger": "🍔", "dish:soup": "🥣", "dish:salad": "🥗",
    "dish:mushroom_cheeseburger": "🍔", "dish:veggie_ramen": "🍲",
    "plate": "🍽️",
    "fx:flame": "🔥", "fx:smoke": "💨", "fx:burst": "✨",
  };

  // Resolve the best asset path for a key, walking the fallback chain. Returns null if none.
  function path(...keys) {
    for (const k of keys) {
      if (k && MANIFEST[k]) return BASE + MANIFEST[k];
    }
    return null;
  }
  // Resolve an emoji for a key chain.
  function emoji(...keys) {
    for (const k of keys) {
      if (k && EMOJI[k]) return EMOJI[k];
    }
    return "❓";
  }

  /* Build an icon element for an entity. Prefers a sprite <img>; on missing/broken image falls
   * back to the emoji. `extraClass` lets callers add a state ring etc. */
  function icon(keyChain, { emojiChain = keyChain, extraClass = "" } = {}) {
    const span = document.createElement("span");
    span.className = "kr-icon " + extraClass;
    const src = path(...keyChain);
    if (src) {
      const img = document.createElement("img");
      img.src = src;
      img.draggable = false;
      const em = emoji(...emojiChain);
      img.onerror = () => { span.textContent = em; };
      span.appendChild(img);
    } else {
      span.textContent = emoji(...emojiChain);
    }
    return span;
  }

  // Convenience builders for the common entity kinds -------------------------
  function stationIcon(type) {
    return icon([`station:${type}`], { emojiChain: [`station:${type}`] });
  }
  function chefIcon(facing = "front", carrying = false) {
    const chain = carrying
      ? [`chef:carry:${facing}`, "chef:carry:front", `chef:${facing}`, "chef:front", "chef"]
      : [`chef:${facing}`, "chef:front", "chef"];
    return icon(chain, { emojiChain: ["chef"] });
  }
  // Held item to composite onto the chef's hands (plate -> dish sprite, else the component).
  function heldIcon(h) {
    return h.state === "PLATE" ? dishIcon(h.ingredient) : componentIcon(h.ingredient, h.state);
  }
  // A held / cooking component (ingredient + state). Adds a state ring + tiny state tag.
  function componentIcon(ingredient, state) {
    const ring = `ring-${(state || "RAW").toLowerCase()}`;
    const node = icon([`ing:${ingredient}:${state}`, `ing:${ingredient}`],
                      { emojiChain: [`ing:${ingredient}`], extraClass: ring });
    const tag = document.createElement("i");
    tag.className = "state-tag";
    tag.textContent = STATE_TAG[state] || "";
    node.appendChild(tag);
    return node;
  }
  function dishIcon(recipe) {
    return icon([`dish:${recipe}`], { emojiChain: [`dish:${recipe}`, "plate"], extraClass: "is-dish" });
  }

  const STATE_TAG = { RAW: "", CHOPPED: "✂", COOKED: "♨", BURNED: "✖", PLATE: "" };

  return { path, emoji, icon, stationIcon, chefIcon, heldIcon, componentIcon, dishIcon,
           MANIFEST, EMOJI };
})();
