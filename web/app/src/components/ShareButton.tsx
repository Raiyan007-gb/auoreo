import { CheckIcon, CopyIcon, Link2Icon, Link2OffIcon, Share2Icon } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { errorText } from "@/lib/utils";

/** The read-only web view for a share token (`/share/:token`; data from `GET /api/shared/{token}`). */
const shareUrl = (token: string) => `${window.location.origin}/share/${encodeURIComponent(token)}`;

/**
 * I5: create / copy / revoke read-only share links for a session. The token is shown once
 * (the server keeps only its hash), so the link lives in this dialog until it closes.
 */
export function ShareButton({ sessionId }: { sessionId: string }) {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const create = () =>
    run(async () => {
      const link = await api.createShare(sessionId);
      setUrl(shareUrl(link.token));
      setNote("Anyone with this link can read this session (secrets are redacted). Copy it now — it is shown once.");
    });
  const revoke = () =>
    run(async () => {
      const r = await api.revokeShares(sessionId);
      setUrl(null);
      setNote(r.revoked ? `Revoked ${r.revoked} link${r.revoked === 1 ? "" : "s"}.` : "There were no active links.");
    });
  const copy = () =>
    url &&
    void navigator.clipboard
      ?.writeText(url)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => setErr("Clipboard is blocked; select the link and copy it."));

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)} data-testid="share-button">
        <Share2Icon aria-hidden /> Share
      </Button>
      <Dialog
        open={open}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) {
            setUrl(null);
            setNote(null);
            setErr(null);
          }
        }}
      >
        <DialogContent data-testid="share-dialog">
          <DialogTitle>Share read-only link</DialogTitle>
          <DialogDescription>
            A link shows this transcript with secrets redacted and no controls. Revoking removes every link of this session.
          </DialogDescription>
          {url && (
            <div className="flex gap-2">
              <Input readOnly value={url} aria-label="Share link" data-testid="share-url" onFocus={(e) => e.currentTarget.select()} />
              <Button variant="outline" size="icon" aria-label={copied ? "Copied" : "Copy link"} onClick={copy} data-testid="share-copy">
                {copied ? <CheckIcon /> : <CopyIcon />}
              </Button>
            </div>
          )}
          {note && (
            <p role="status" className="text-xs text-muted-foreground" data-testid="share-note">
              {note}
            </p>
          )}
          {err && (
            <p role="alert" className="text-xs text-destructive">
              {err}
            </p>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => void revoke()} disabled={busy} data-testid="share-revoke">
              <Link2OffIcon aria-hidden /> Revoke links
            </Button>
            <DialogClose asChild>
              <Button variant="ghost">Close</Button>
            </DialogClose>
            <Button variant="brand" onClick={() => void create()} disabled={busy} data-testid="share-create">
              <Link2Icon aria-hidden /> Create link
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
