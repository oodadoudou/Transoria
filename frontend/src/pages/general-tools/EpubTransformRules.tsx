import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { useMessages } from "@/locales";
import styles from "./EpubContentPage.module.css";

export type TransformRule = Record<string, string>;
export const initialRule = (kind: "css" | "html"): TransformRule => kind === "css"
  ? { id: crypto.randomUUID(), property: "font-size", operator: "any", match: "", action: "multiply", value: "1.2", target: "" }
  : { id: crypto.randomUUID(), selector: "p", action: "add_class", target: "", value: "edited" };

export function EpubTransformRules({ kind, rules, change, disabled }: {
  kind: "css" | "html"; rules: TransformRule[]; change: (rules: TransformRule[]) => void; disabled: boolean;
}) {
  const labels = useMessages().epubContentTool.transformLabels;
  const t = useMessages().epubContentTool;
  const actions = kind === "css" ? ["set", "remove", "rename", "multiply", "add"] : ["rename", "wrap", "unwrap", "remove", "set_attr", "remove_attr", "add_class", "remove_class"];
  const update = (index: number, key: string, value: string) => change(rules.map((rule, position) => index === position ? { ...rule, [key]: value } : rule));
  const move = (index: number, direction: number) => { const next = [...rules]; [next[index], next[index + direction]] = [next[index + direction], next[index]]; change(next); };
  return <div className={styles.transformRules}>
    {rules.map((rule, index) => <fieldset key={rule.id} disabled={disabled} className={styles.transformRule}>
      <legend>{index + 1}</legend>
      <div className={styles.toolControls}>
        <label>{kind === "css" ? labels.property : labels.selector}<input value={kind === "css" ? rule.property : rule.selector} onChange={(event) => update(index, kind === "css" ? "property" : "selector", event.target.value)} /></label>
        {kind === "css" ? <><label>{labels.condition}<select value={rule.operator} onChange={(event) => update(index, "operator", event.target.value)}>{["any", "equals", "contains", "regex"].map((operator) => <option key={operator} value={operator}>{labels[operator]}</option>)}</select></label><label>{labels.match}<input disabled={rule.operator === "any"} value={rule.match} onChange={(event) => update(index, "match", event.target.value)} /></label></> : null}
      </div>
      <div className={styles.toolControls}>
        <label>{labels.action}<select value={rule.action} onChange={(event) => update(index, "action", event.target.value)}>{actions.map((action) => <option key={action} value={action}>{labels[action]}</option>)}</select></label>
        {!(["unwrap", "remove", "add_class", "remove_class"].includes(rule.action)) ? <label>{labels.target}<input value={rule.target} onChange={(event) => update(index, "target", event.target.value)} /></label> : null}
        {!(["unwrap", "remove", "rename", "wrap", "remove_attr"].includes(rule.action)) ? <label>{labels.value}<input value={rule.value} onChange={(event) => update(index, "value", event.target.value)} /></label> : null}
        <button type="button" aria-label={`${t.up}: ${index + 1}`} disabled={!index} onClick={() => move(index, -1)}><ArrowUp size={16} /></button>
        <button type="button" aria-label={`${t.down}: ${index + 1}`} disabled={index === rules.length - 1} onClick={() => move(index, 1)}><ArrowDown size={16} /></button>
        <button type="button" aria-label={`${t.removeEntry}: ${index + 1}`} onClick={() => change(rules.filter((_, position) => position !== index))}><Trash2 size={16} /></button>
      </div>
    </fieldset>)}
    <button type="button" disabled={disabled || rules.length >= 100} onClick={() => change([...rules, initialRule(kind)])}><Plus size={16} />{labels.addRule}</button>
  </div>;
}
