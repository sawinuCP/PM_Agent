import { Bot, BrainCircuit, BotMessageSquare, Database, FileText, Globe,
  Map, MessageSquareText, Settings2, Wrench } from "lucide-react";

/** Agent-name → icon/color: single source of truth for chat, graph, trace. */
export function agentIcon(agent: string): { Icon: typeof Bot; color: string } {
  const a = agent.toLowerCase();
  if (a.includes("jira")) return { Icon: Bot, color: "var(--info)" };
  if (a.includes("data") || a.includes("scientist") || a.includes("sandbox"))
    return { Icon: BrainCircuit, color: "var(--violet)" };
  if (a.includes("devops")) return { Icon: Settings2, color: "var(--warn)" };
  if (a.includes("conversational")) return { Icon: MessageSquareText, color: "var(--accent-strong)" };
  return { Icon: Bot, color: "var(--accent-strong)" };
}

export function toolIcon(tool: string): { Icon: typeof Wrench; color: string } {
  const t = tool.toLowerCase();
  if (t.includes("python") || t.includes("code") || t.includes("sandbox"))
    return { Icon: Database, color: "var(--violet)" };
  if (t.includes("jira") || t.includes("search")) return { Icon: Globe, color: "var(--info)" };
  if (t.includes("write") || t.includes("create") || t.includes("update"))
    return { Icon: FileText, color: "var(--warn)" };
  return { Icon: Wrench, color: "var(--accent-strong)" };
}
