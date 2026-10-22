import { CircleAlert } from "lucide-react";

interface Props {
  tool: string;
  reason?: string;
  simple?: boolean;
  onApprove: () => void;
  onReject: () => void;
}

/** Human-in-the-loop approval card (event: approval_required). */
export function ApprovalCard({ tool, reason, simple = false, onApprove, onReject }: Props) {
  return (
    <div className="approval" role="alert">
      <div className="approval-icon"><CircleAlert size={17} /></div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="approval-title">{simple ? "Your approval is needed" : "Human approval required"}</div>
        <div className="approval-desc">
          {simple ? "Azend wants to perform a protected action" : "Sensitive tool"}{" "}
          <code>{tool.replaceAll("_", " ")}</code>
          {simple ? ". Allow it to continue?" : " is parked awaiting your decision."}
          {reason ? ` ${reason}` : ""}
        </div>
      </div>
      <div className="approval-actions">
        <button className="btn btn-primary" onClick={onApprove}>{simple ? "Allow" : "Approve"}</button>
        <button className="btn" onClick={onReject}>{simple ? "Don't allow" : "Reject"}</button>
      </div>
    </div>
  );
}
