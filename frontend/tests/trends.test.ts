import assert from "node:assert/strict";
import test from "node:test";
import { curveSegments, pointValue, rangeFor, shiftDay, filterTrendChoices, type TrendPoint, type TrendChoice } from "../src/lib/trends.ts";

function sample(rank: number | null, value: number | null, connected = true, status = "present", compatible = true) {
  return { rank, status, metric: { value }, connectable_before: connected, metric_compatible: compatible } as TrendPoint;
}
test("真实零值有点，缺失、离榜和失败不变成零或跨缺口连线", () => {
  const points = [sample(1, 0, false), sample(2, 20), sample(null, null, false, "failed"), sample(4, 30, false), sample(null, null, false, "not_on_board"), sample(5, 40, false)];
  assert.equal(pointValue(points[0], "metric"), 0);
  assert.equal(pointValue(points[2], "metric"), null);
  assert.deepEqual(curveSegments(points, "rank").map(s => s.length), [2, 1, 1]);
  assert.deepEqual(curveSegments(points, "metric").map(s => s.length), [2, 1, 1]);
});
test("缓存、部分解析和缺失指标断开曲线，不改写原始数据", () => {
  const points = [sample(1, 10, false), sample(2, null, false), sample(3, 20, false), sample(4, 30)];
  const original = JSON.stringify(points);
  assert.deepEqual(curveSegments(points, "metric").map(s => s.length), [1, 2]);
  assert.equal(pointValue(sample(1, 9, false, "present", false), "metric"), null);
  assert.equal(JSON.stringify(points), original);
});
test("七天包含基准日，跨月跨年不使用本机时区偏移", () => {
  assert.equal(shiftDay("2026-01-01", -1), "2025-12-31");
  assert.deepEqual(rangeFor("seven", "2026-10-14", "", ""), { start: "2026-10-08", end: "2026-10-14" });
  assert.deepEqual(rangeFor("previous", "2026-10-14", "", ""), { start: "2026-10-13", end: "2026-10-13" });
  assert.deepEqual(rangeFor("custom", "2026-10-14", "2026-10-12", "2026-10-13"), { start: "2026-10-12", end: "2026-10-13" });
});
test("趋势筛选搜索来源标题并限制真实平台，不创建示例候选", () => {
  const rows = [{ id: "a", title: "大桥", source_titles: ["新鸭绿江大桥开通"], platforms: ["douyin", "weibo"] }, { id: "b", title: "演唱会", source_titles: [], platforms: ["weibo"] }] as TrendChoice[];
  assert.deepEqual(filterTrendChoices(rows, "鸭绿江", "douyin").map(e => e.id), ["a"]);
  assert.equal(filterTrendChoices(rows, "演唱会", "douyin").length, 0);
  assert.equal(filterTrendChoices(rows, "不存在", "all").length, 0);
});
