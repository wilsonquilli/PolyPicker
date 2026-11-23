import { Fragment, type ReactNode } from "react";

type Node = ReactNode | ((children: ReactNode) => ReactNode);

/**
 * Translated sentences with markup: `rich(t("Tap <b>Add</b> then {icon}"), { icon: <Icon /> })`.
 * `<b>`/`<i>` render as-is; other tags (`<link>…</link>`) call the matching function in `nodes`; `{name}` inserts a node.
 * Keeps whole sentences translatable instead of splitting them around bold words.
 */
export function rich(text: string, nodes: Record<string, Node> = {}): ReactNode {
  const out: ReactNode[] = [];
  const re = /<(\w+)>(.*?)<\/\1>|\{(\w+)\}/g;
  let last = 0, m: RegExpExecArray | null, k = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) {
      const fn = nodes[m[1]];
      const inner = rich(m[2], nodes);
      out.push(<Fragment key={k++}>{typeof fn === "function" ? fn(inner) : m[1] === "i" ? <i>{inner}</i> : <b>{inner}</b>}</Fragment>);
    } else {
      const n = nodes[m[3]];
      out.push(<Fragment key={k++}>{typeof n === "function" ? n(null) : n ?? m[0]}</Fragment>);
    }
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
