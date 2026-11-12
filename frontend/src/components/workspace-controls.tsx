"use client";
import { useState } from "react";
import { ListFilter, FolderPlus, Settings2, X } from "lucide-react";
import { CATEGORIES, type Preferences } from "../lib/workspace";

type Props = { preferences: Preferences; update: (change: Partial<Preferences>) => void; wall: boolean };
export default function WorkspaceControls({ preferences: p, update, wall }: Props) {
  const [editing, setEditing] = useState(false);
  const [groupName, setGroupName] = useState("");
  const [message, setMessage] = useState("");
  const group = p.groups.find(g => g.id === p.activeGroup);
  function addGroup() {
    const name = groupName.trim();
    if (!name || p.groups.some(g => g.name === name)) { setMessage("请输入不重复的分组名称。"); return; }
    if (p.groups.length >= 12) { setMessage("最多保存12个分组。"); return; }
    const id = "g_" + crypto.randomUUID();
    update({ groups: [...p.groups, { id, name, boardIds: [] }], activeGroup: id });
    setGroupName(""); setMessage("");
  }
  return <section className="hr-workbench-controls" aria-label="工作台筛选与分组">
    <div className="hr-group-row">
      {[{ id: "all", name: "全部榜单" }, { id: "favorites", name: "已关注" }, ...p.groups].map(g => <button key={g.id} className={"hr-chip " + (p.activeGroup === g.id ? "is-selected" : "")} onClick={() => update({ activeGroup: g.id })}>{g.name}</button>)}
      {!wall && <button className="hr-link-button" onClick={() => setEditing(!editing)}><Settings2 />管理分组</button>}
    </div>
    {editing && !wall && <div className="hr-group-editor">
      <label>新分组名称<input aria-label="新分组名称" maxLength={24} value={groupName} onChange={e => setGroupName(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.nativeEvent.isComposing) addGroup(); }} placeholder="例如：重点观察" /></label>
      <button className="hr-button" onClick={addGroup}><FolderPlus />创建分组</button>
      {group && <><label>修改分组名称<input aria-label="修改分组名称" maxLength={24} value={group.name} onChange={e => {
        const name = e.target.value;
        if (name.trim() && !p.groups.some(g => g.id !== group.id && g.name === name.trim())) update({ groups: p.groups.map(g => g.id === group.id ? { ...g, name } : g) });
      }} /></label><div className="hr-group-members">{[["douyin-hot", "抖音"], ["weibo-hot", "微博"], ["baidu-hot", "百度"]].map(([id, name]) => <label key={id}><input type="checkbox" aria-label={"分组包含" + name} checked={group.boardIds.includes(id)} onChange={e => update({ groups: p.groups.map(g => g.id === group.id ? { ...g, boardIds: e.target.checked ? [...g.boardIds, id] : g.boardIds.filter(b => b !== id) } : g) })} />{name}</label>)}</div>
        <button className="hr-button" onClick={() => update({ groups: p.groups.filter(g => g.id !== group.id), activeGroup: "all" })}><X />删除此分组</button></>}
      <small>分组、顺序和筛选只保存于当前浏览器；删除分组不删除热榜或历史。</small>
      {message && <p role="alert">{message}</p>}
    </div>}
    <div className="hr-filter-fields">
      <label><ListFilter />关键词包含<input aria-label="包含关键词" maxLength={300} value={p.include} placeholder="任一命中，逗号分隔" onChange={e => update({ include: e.target.value })} /></label>
      <label>关键词排除<input aria-label="排除关键词" maxLength={300} value={p.exclude} placeholder="任一命中即排除" onChange={e => update({ exclude: e.target.value })} /></label>
      <label className="hr-checkbox"><input type="checkbox" checked={p.hideAds} onChange={e => update({ hideAds: e.target.checked })} />隐藏广告</label>
      <label className="hr-checkbox"><input type="checkbox" checked={p.risingOnly} onChange={e => update({ risingOnly: e.target.checked })} />只看榜位上升</label>
      <button className="hr-link-button" onClick={() => update({ source: "all", category: "全部分类", query: "", include: "", exclude: "", hideAds: false, risingOnly: false })}>清除筛选</button>
    </div>
    <div className="hr-category-row">{CATEGORIES.map(name => <button key={name} className={"hr-chip " + (p.category === name ? "is-selected" : "")} onClick={() => update({ category: name })}>{name}</button>)}<small>分类依据本机关键词规则，可能有误；保留源内榜位。</small></div>
  </section>;
}
