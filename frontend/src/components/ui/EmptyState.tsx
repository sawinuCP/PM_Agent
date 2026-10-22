import type { ComponentType } from "react";

interface Props {
  icon: ComponentType<{ size?: number | string }>;
  title: string;
  desc?: string;
  action?: React.ReactNode;
}

/** Shared empty/error/placeholder block — same visual language everywhere. */
export function EmptyState({ icon: Icon, title, desc, action }: Props) {
  return (
    <div className="state-block">
      <div className="state-icon"><Icon size={18} /></div>
      <div className="state-title">{title}</div>
      {desc && <div className="state-desc">{desc}</div>}
      {action && <div className="state-action">{action}</div>}
    </div>
  );
}
