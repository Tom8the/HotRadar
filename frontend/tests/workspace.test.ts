import { test } from "node:test";
import assert from "node:assert/strict";
import { defaults, normalizePreferences, reorder, filterItems, visibleBoards, selectedPlatformBoard } from "../src/lib/workspace.ts";
import type { Board, HotItem } from "../src/lib/types";

test("M1 migration preserves favorites and rejects unknown sources/corrupt preferences", () => {
  const migrated = normalizePreferences({ favorites: ["weibo-hot", "baidu", "weibo-hot"], dark: true });
  assert.deepEqual(migrated.favorites, ["weibo-hot"]); assert.equal(migrated.dark, true);
  assert.deepEqual(normalizePreferences(null).order, ["douyin-hot", "weibo-hot", "baidu-hot"]);
  const bad = normalizePreferences({ order: ["weibo-hot", "weibo-hot", "evil"], source: "unknown", activeGroup: "missing", hideAds: "false" });
  assert.deepEqual(bad.order, ["weibo-hot", "douyin-hot", "baidu-hot"]); assert.equal(bad.source, "all"); assert.equal(bad.activeGroup, "all"); assert.equal(bad.hideAds, false);
});
test("group deletion/corruption returns to all without deleting favorite boards", () => {
  const p = normalizePreferences({ groups: [{ id: "g_ok", name: "观察", boardIds: ["douyin-hot", "rss"] }, { id: "bad", name: "bad", boardIds: [] }], activeGroup: "g_deleted", favorites: ["weibo-hot"] });
  assert.equal(p.activeGroup, "all"); assert.equal(p.groups.length, 1); assert.deepEqual(p.groups[0].boardIds, ["douyin-hot"]); assert.deepEqual(p.favorites, ["weibo-hot"]);
});
test("Baidu is appended to saved order and supports filters, favorites and groups", () => {
  const p = normalizePreferences({ order: ["weibo-hot", "douyin-hot"], source: "baidu", favorites: ["baidu-hot"], groups: [{ id: "g_baidu", name: "搜索热点", boardIds: ["baidu-hot"] }], activeGroup: "g_baidu" });
  assert.deepEqual(p.order, ["weibo-hot", "douyin-hot", "baidu-hot"]);
  assert.equal(p.source, "baidu");
  const board = { id: "baidu-hot", source_id: "baidu" } as Board;
  assert.deepEqual(visibleBoards([board, { id: "weibo-hot", source_id: "weibo" } as Board], p, true), [board]);
  assert.equal(selectedPlatformBoard(board, [board], p), board);
});
test("reorder never mutates the original order and ignores foreign drag payload", () => {
  const old = ["douyin-hot", "weibo-hot"];
  assert.deepEqual(reorder(old, "douyin-hot", "weibo-hot"), ["weibo-hot", "douyin-hot"]);
  assert.deepEqual(old, ["douyin-hot", "weibo-hot"]); assert.deepEqual(reorder(old, "rss", "weibo-hot"), old);
});
function item(title: string, rank: number | null, flags: string[] = [], change: number | null = null): HotItem {
  return { id: title, external_id: title, title, rank, display_order: 7, flags, rank_change: change,
    url: "https://example.test/", item_kind: "topic", is_pinned: false, metrics: { heat: { value: null, unit: "热度", provider: "test", definition: "test" } }, first_observed_at: "2026-10-14T00:00:00Z", published_at: null, source_event_at: null };
}
test("keyword/ad filters preserve source rank, metric null and original array", () => {
  const items = [item("AI新模型", 7), item("AI电影广告", null, ["广告"]), item("AI玩具", 22)];
  const result = filterItems(items, { ...defaults, include: "ai,玩具", exclude: "玩具", hideAds: true }, " AI ");
  assert.equal(result.length, 1); assert.equal(result[0].rank, 7); assert.equal(result[0].metrics.heat.value, null); assert.equal(items.length, 3);
});
test("rising-only never treats missing comparison as zero or increase", () => {
  const items = [item("无前次记录", 1), item("上升", 12, [], 3), item("下降", 4, [], -2)];
  assert.deepEqual(filterItems(items, { ...defaults, risingOnly: true }, "").map(i => i.title), ["上升"]);
});
test("groups and source filter intersect without changing underlying source order", () => {
  const boards = [{ id: "douyin-hot", source_id: "douyin" }, { id: "weibo-hot", source_id: "weibo" }] as Board[];
  const p = normalizePreferences({ order: ["weibo-hot", "douyin-hot"], groups: [{ id: "g_watch", name: "重点", boardIds: ["weibo-hot"] }], activeGroup: "g_watch" });
  assert.deepEqual(visibleBoards(boards, p, false).map(b => b.id), ["weibo-hot"]);
  assert.deepEqual(visibleBoards(boards, { ...p, source: "douyin" }, false), []);
  assert.equal(boards[0].id, "douyin-hot");
});

test("Douyin city selection persists without adding a duplicate platform card or losing groups", () => {
  const p = normalizePreferences({ douyinBoard: "douyin-wuxi", favorites: ["douyin-hot"], groups: [{id:"g_local",name:"本地",boardIds:["douyin-hot"]}], activeGroup:"g_local" });
  assert.equal(p.douyinBoard, "douyin-wuxi");
  assert.equal(normalizePreferences({douyinBoard:"beijing"}).douyinBoard, "douyin-hot");
  const boards = [{id:"douyin-hot",source_id:"douyin"},{id:"douyin-wuxi",source_id:"douyin"},{id:"weibo-hot",source_id:"weibo"}] as Board[];
  assert.deepEqual(visibleBoards(boards,p,true).map(b=>b.id),["douyin-hot"]);
});

test("Weibo city preference remains independent from Douyin and platform groups", () => {
  const p = normalizePreferences({ weiboBoard: "weibo-wuxi", douyinBoard: "douyin-hot", favorites: ["weibo-hot"], groups: [{id:"g_local",name:"本地",boardIds:["weibo-hot"]}], activeGroup:"g_local" });
  assert.equal(p.weiboBoard, "weibo-wuxi");
  assert.equal(p.douyinBoard, "douyin-hot");
  assert.equal(normalizePreferences({weiboBoard:"beijing"}).weiboBoard, "weibo-hot");
  const boards = [{id:"weibo-hot",source_id:"weibo"},{id:"weibo-wuxi",source_id:"weibo"},{id:"douyin-hot",source_id:"douyin"}] as Board[];
  assert.deepEqual(visibleBoards(boards,p,true).map(b=>b.id),["weibo-hot"]);
});

test("missing Weibo city data cannot fall back to a national snapshot or session state", () => {
  const national = {id:"weibo-hot",source_id:"weibo",snapshot:{items:[item("全国话题",1)]},latest_run:{id:"national"},last_success_at:"2026-10-15",enabled:true} as Board;
  const p = normalizePreferences({weiboBoard:"weibo-wuxi"});
  const missing = selectedPlatformBoard(national,[national],p);
  assert.equal(missing.id,"weibo-wuxi"); assert.equal(missing.snapshot,null);
  assert.equal(missing.latest_run,null); assert.equal(missing.last_success_at,null);
  const city = {...missing,snapshot:{items:[item("同城话题",3)]}} as Board;
  assert.equal(selectedPlatformBoard(national,[national,city],p),city);
  assert.equal(selectedPlatformBoard(national,[national,city],defaults),national);
});

test("native Douyin city board persists separately and cannot display creator or national data as fallback", () => {
  const p = normalizePreferences({douyinBoard:"douyin-wuxi-app",favorites:["douyin-hot"]});
  assert.equal(p.douyinBoard,"douyin-wuxi-app");
  assert.equal(normalizePreferences({douyinBoard:"douyin-wuxi"}).douyinBoard,"douyin-wuxi");
  const national = {id:"douyin-hot",source_id:"douyin",snapshot:{items:[item("全国话题",1)]},latest_run:{id:"national"},last_success_at:"2026-10-19",enabled:true} as Board;
  const creator = {...national,id:"douyin-wuxi",snapshot:{items:[item("创作话题",1082)]}} as Board;
  const missing = selectedPlatformBoard(national,[national,creator],p);
  assert.equal(missing.id,"douyin-wuxi-app"); assert.equal(missing.snapshot,null);
  assert.equal(missing.latest_run,null); assert.equal(missing.last_success_at,null);
  assert.equal(missing.metric_label,"同城热度");
  const city = {...missing,snapshot:{items:[item("真实城市话题",1)]}} as Board;
  assert.equal(selectedPlatformBoard(national,[national,creator,city],p),city);
  assert.deepEqual(visibleBoards([national,creator,city],p,true).map(b=>b.id),["douyin-hot"]);
});
