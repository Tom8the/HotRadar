import type { Board, HotItem } from "./types";

export const BOARD_IDS = ["douyin-hot", "weibo-hot", "baidu-hot"];
export const CATEGORIES = ["全部分类", "科技", "财经", "体育", "娱乐", "生活", "社会", "其他"];
export type Group = { id: string; name: string; boardIds: string[] };
export type Preferences = {
  version: 2; favorites: string[]; dark: boolean; order: string[]; groups: Group[];
  activeGroup: string; source: string; category: string; query: string; include: string; exclude: string;
  hideAds: boolean; risingOnly: boolean; autoRefresh: boolean;
  douyinBoard: "douyin-hot" | "douyin-wuxi" | "douyin-wuxi-app";
  weiboBoard: "weibo-hot" | "weibo-wuxi";
};
export const defaults: Preferences = {
  version: 2, favorites: [], dark: false, order: [...BOARD_IDS], groups: [], activeGroup: "all",
  source: "all", category: "全部分类", query: "", include: "", exclude: "", hideAds: false, risingOnly: false, autoRefresh: true,
  douyinBoard: "douyin-hot",
  weiboBoard: "weibo-hot",
};
const ids = (value: unknown) => Array.isArray(value) ? [...new Set(value.filter(x => typeof x === "string" && BOARD_IDS.includes(x)))] : [];
const text = (value: unknown, length: number) => typeof value === "string" ? value.slice(0, length) : "";

export function normalizePreferences(value: unknown): Preferences {
  if (!value || typeof value !== "object" || Array.isArray(value)) return { ...defaults, order: [...BOARD_IDS], groups: [], favorites: [] };
  const p = value as Record<string, unknown>;
  const groups: Group[] = [];
  if (Array.isArray(p.groups)) for (const candidate of p.groups.slice(0, 12)) {
    if (!candidate || typeof candidate !== "object") continue;
    const group = candidate as Record<string, unknown>;
    const id = text(group.id, 60), name = text(group.name, 24).trim();
    if (/^g_[a-zA-Z0-9-]+$/.test(id) && name && !groups.some(g => g.id === id || g.name === name)) groups.push({ id, name, boardIds: ids(group.boardIds) });
  }
  const order = ids(p.order);
  return {
    version: 2, favorites: ids(p.favorites), dark: typeof p.dark === "boolean" ? p.dark : false,
    order: [...order, ...BOARD_IDS.filter(id => !order.includes(id))], groups,
    activeGroup: p.activeGroup === "favorites" || groups.some(g => g.id === p.activeGroup) ? p.activeGroup as string : "all",
    source: ["douyin", "weibo", "baidu"].includes(p.source as string) ? p.source as string : "all",
    category: CATEGORIES.includes(p.category as string) ? p.category as string : "全部分类",
    query: text(p.query, 300), include: text(p.include, 300), exclude: text(p.exclude, 300),
    hideAds: p.hideAds === true, risingOnly: p.risingOnly === true, autoRefresh: p.autoRefresh !== false,
    douyinBoard: p.douyinBoard === "douyin-wuxi-app" ? "douyin-wuxi-app" : p.douyinBoard === "douyin-wuxi" ? "douyin-wuxi" : "douyin-hot",
    weiboBoard: p.weiboBoard === "weibo-wuxi" ? "weibo-wuxi" : "weibo-hot",
  };
}

export function reorder(order: string[], from: string, to: string): string[] {
  if (from === to || !order.includes(from) || !order.includes(to)) return order;
  const next = order.filter(id => id !== from);
  next.splice(order.indexOf(to), 0, from);
  return next;
}

const rules: [string, RegExp][] = [
  ["科技", /\bai\b|人工智能|机器人|芯片|模型|鸿蒙|苹果|openai|科技|智能|航天|卫星|算力/i],
  ["财经", /a股|股市|股票|指数|基金|经济|外贸|投资|金融|关税|营收|人民币|黄金|银行|涨停|跌停/i],
  ["体育", /足球|篮球|nba|cba|欧冠|世界杯|奥运|夺冠|联赛|赛程|斯诺克|巴萨|曼联|拜仁|国米|英雄联盟|无畏契约|lpl|王者荣耀|浙ba/i],
  ["娱乐", /演唱会|演员|明星|电影|电视剧|票房|短剧|综艺|肖战|白鹿|金晨|丞磊|宋亚轩|毛晓彤|张律|翻跳/i],
  ["生活", /穿搭|美食|萌宠|宝宝|秋冬|秋日|奶茶|旅游|桂花|健身|熬夜|咖啡|食谱|宠物/i],
  ["社会", /通报|地震|大桥|高铁|救援|谣言|警方|广交会|粮食|秋粮|战役|事故|教育|学校|急救|违法/i],
];
export function categoryOf(title: string) { return rules.find(([, rule]) => rule.test(title))?.[0] ?? "其他"; }
export function tokens(value: string) { return value.toLocaleLowerCase().split(/[\s,，;；、]+/).filter(Boolean).slice(0, 30); }

export function filterItems(items: HotItem[], p: Preferences, query: string): HotItem[] {
  const includes = tokens(p.include), excludes = tokens(p.exclude), search = query.trim().toLocaleLowerCase();
  return items.filter(item => {
    const title = item.title.toLocaleLowerCase();
    return (!search || title.includes(search)) &&
      (!includes.length || includes.some(token => title.includes(token))) &&
      !excludes.some(token => title.includes(token)) &&
      (!p.hideAds || !item.flags.includes("广告")) &&
      (!p.risingOnly || (item.rank_change !== null && item.rank_change > 0)) &&
      (p.category === "全部分类" || categoryOf(item.title) === p.category);
  });
}

export function visibleBoards(boards: Board[], p: Preferences, following: boolean): Board[] {
  const group = p.groups.find(g => g.id === p.activeGroup);
  return boards.filter(b => BOARD_IDS.includes(b.id) && (p.source === "all" || p.source === b.source_id) &&
    (!following || p.favorites.includes(b.id)) &&
    (p.activeGroup !== "favorites" || p.favorites.includes(b.id)) &&
    (!group || group.boardIds.includes(b.id)))
    .sort((a, b) => p.order.indexOf(a.id) - p.order.indexOf(b.id));
}

export function selectedPlatformBoard(card: Board, boards: Board[], p: Preferences): Board {
  const id = card.id === "weibo-hot" ? p.weiboBoard : card.id === "douyin-hot" ? p.douyinBoard : card.id;
  if (id === card.id) return card;
  return boards.find(b => b.id === id) || {
    ...card, id, name: card.source_id === "weibo" ? "无锡榜 · 同城热搜" : id === "douyin-wuxi-app" ? "无锡榜 · 抖音原生城市榜" : "无锡创作热点 · 创作者中心",
    metric_name: "city_heat", metric_label: id === "douyin-wuxi" ? "同城话题热度" : "同城热度",
    snapshot: null, latest_run: null, state: "pending", last_success_at: null, enabled: false,
  };
}
