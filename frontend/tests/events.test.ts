import assert from "node:assert/strict";
import test from "node:test";
import { filterEvents, type RadarEvent } from "../src/lib/events.ts";

const rows = [
  { id: "a", title: "大桥将开通", platforms: ["douyin", "weibo"], manual_locked: false,
    followed: true, tags: ["交通"], evidence: [{ title: "新鸭绿江大桥" }] },
  { id: "b", title: "学校新闻", platforms: ["weibo"], manual_locked: true,
    followed: false, tags: ["娱乐"], evidence: [{ title: "金晨自曝学校倒闭" }] },
  { id: "c", title: "演唱会", platforms: ["douyin"], manual_locked: false,
    followed: true, tags: [], evidence: [{ title: "肖战官宣演唱会" }] },
] as RadarEvent[];

test("事件搜索包含原始来源标题，保留当前关系与顺序", () => {
  assert.deepEqual(filterEvents(rows, "金晨", "all", "").map(e => e.id), ["b"]);
  assert.deepEqual(filterEvents(rows, "  ", "all", "").map(e => e.id), ["a", "b", "c"]);
  assert.equal(filterEvents(rows, "不存在的关键词", "all", "").length, 0);
});
test("跨平台、人工与关注分别过滤，不把同一平台多条来源算跨平台", () => {
  assert.deepEqual(filterEvents(rows, "", "cross", "").map(e => e.id), ["a"]);
  assert.deepEqual(filterEvents(rows, "", "manual", "").map(e => e.id), ["b"]);
  assert.deepEqual(filterEvents(rows, "", "followed", "").map(e => e.id), ["a", "c"]);
});
test("标签和搜索、关注条件取交集，不改写事件对象", () => {
  const before = JSON.stringify(rows);
  assert.deepEqual(filterEvents(rows, "大桥", "followed", "交通").map(e => e.id), ["a"]);
  assert.equal(filterEvents(rows, "学校", "followed", "娱乐").length, 0);
  assert.equal(JSON.stringify(rows), before);
});
