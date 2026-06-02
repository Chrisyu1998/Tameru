/**
 * Closed category list — mirrors backend `ALLOWED_CATEGORIES` exactly
 * (`app/prompts/categories.py`). The backend is the source of truth: the
 * MCC-aligned taxonomy here is what drives card-reward multiplier matching
 * (DESIGN.md §6.2). Any drift here re-introduces the 422 from
 * /transactions/confirm that Day 10b was created to fix.
 */

import type { SketchIconKind } from "@/components/SketchIcon";
import { useAppStore, type UiLanguage } from "@/store";

export const CATEGORIES = [
  "Groceries",
  "Dining",
  "Coffee Shops",
  "Gas",
  "Transit",
  "Travel",
  "Streaming",
  "Memberships",
  "Entertainment",
  "Shopping",
  "Drugstores",
  "Home",
  "Utilities",
  "Health",
  "Other",
] as const;

export type Category = (typeof CATEGORIES)[number];

/**
 * Localized *display* labels per category, keyed by UI language (DESIGN.md
 * §6.6 Tier 2). The STORED value is always the English enum above — it's the
 * join key, the glyph key, and the contract-test key (DESIGN.md §6.2), and is
 * what every tool call / PATCH sends. Only the rendered text is translated.
 *
 * Drafts for ja / zh-TW — native speakers refine. `en` is the identity map so
 * a single lookup path covers all languages. Traditional Chinese only.
 */
export const CATEGORY_LABELS: Record<"en" | "ja" | "zh-TW", Record<Category, string>> = {
  en: {
    Groceries: "Groceries",
    Dining: "Dining",
    "Coffee Shops": "Coffee Shops",
    Gas: "Gas",
    Transit: "Transit",
    Travel: "Travel",
    Streaming: "Streaming",
    Memberships: "Memberships",
    Entertainment: "Entertainment",
    Shopping: "Shopping",
    Drugstores: "Drugstores",
    Home: "Home",
    Utilities: "Utilities",
    Health: "Health",
    Other: "Other",
  },
  ja: {
    Groceries: "食料品",
    Dining: "外食",
    "Coffee Shops": "カフェ",
    Gas: "ガソリン",
    Transit: "交通",
    Travel: "旅行",
    Streaming: "ストリーミング",
    Memberships: "会員費",
    Entertainment: "娯楽",
    Shopping: "ショッピング",
    Drugstores: "ドラッグストア",
    Home: "住居",
    Utilities: "公共料金",
    Health: "健康",
    Other: "その他",
  },
  "zh-TW": {
    Groceries: "食品雜貨",
    Dining: "餐飲",
    "Coffee Shops": "咖啡",
    Gas: "加油",
    Transit: "交通",
    Travel: "旅遊",
    Streaming: "串流",
    Memberships: "會員",
    Entertainment: "娛樂",
    Shopping: "購物",
    Drugstores: "藥妝店",
    Home: "居家",
    Utilities: "水電費",
    Health: "健康",
    Other: "其他",
  },
};

/**
 * Resolve the store's `UiLanguage` (which may be null/undefined before an
 * explicit choice) to a concrete label-map key, defaulting to English.
 */
function labelLang(lang: UiLanguage): "en" | "ja" | "zh-TW" {
  return lang === "ja" || lang === "zh-TW" ? lang : "en";
}

/**
 * Non-reactive category-label lookup — mirrors `formatMoney`'s store read for
 * callers outside React render (or where a re-render on language change isn't
 * needed). Returns the raw value unchanged for anything not in the enum.
 */
export function categoryLabel(category: Category | string): string {
  const map = CATEGORY_LABELS[labelLang(useAppStore.getState().uiLanguage)];
  return (map as Record<string, string>)[category] ?? String(category);
}

/**
 * Reactive category-label lookup. Subscribes to the UI language so consumers
 * re-render and re-label the moment the user switches language in Settings —
 * the formatting helpers stay non-reactive, but category labels are the one
 * surface a language switch must update live (DESIGN.md §6.6 Tier 2).
 *
 * Usage: `const label = useCategoryLabel(); ... {label(t.category)}`
 */
export function useCategoryLabel(): (category: Category | string) => string {
  const lang = labelLang(useAppStore((s) => s.uiLanguage));
  const map = CATEGORY_LABELS[lang] as Record<string, string>;
  return (category) => map[category] ?? String(category);
}

/** Color tokens per category, used by the donut + category list. */
export const CATEGORY_TINT: Record<Category, string> = {
  Groceries: "var(--moss)",
  Dining: "var(--over)",
  "Coffee Shops": "var(--over)",
  Gas: "var(--warn)",
  Transit: "var(--ink-secondary)",
  Travel: "var(--moss-deep)",
  Streaming: "var(--ink-tertiary)",
  Memberships: "var(--ink-quaternary)",
  Entertainment: "var(--warn)",
  Shopping: "var(--ink-tertiary)",
  Drugstores: "var(--moss)",
  Home: "var(--moss-deep)",
  Utilities: "var(--warn)",
  Health: "var(--moss)",
  Other: "var(--ink-quaternary)",
};

/**
 * Hand-drawn sketch glyph per category. Used on /breakdown row tiles
 * to add personality to a list-heavy page. Each category gets a glyph
 * that visually matches its meaning — `heart` for Health is the only
 * cross-category reuse (it's the universal symbol).
 */
export const CATEGORY_SKETCH: Record<Category, SketchIconKind> = {
  Groceries: "bag",
  Dining: "fork",
  "Coffee Shops": "coffee-mug",
  Gas: "fuel-pump",
  Transit: "car",
  Travel: "plane",
  Streaming: "play",
  Memberships: "badge",
  Entertainment: "popcorn",
  Shopping: "cart",
  Drugstores: "pill",
  Home: "home",
  Utilities: "bolt",
  Health: "heart",
  Other: "dot",
};
