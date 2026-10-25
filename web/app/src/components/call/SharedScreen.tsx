import { KeyboardIcon } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { type InputEvent, relCoords } from "@/lib/rtc";

/**
 * Local preview of the camera / shared screen. With remote control on (teleop allowed by the
 * server and switched on by the user) clicks on the shared-screen view are sent as
 * `{op:"click", x, y}` (0–1000 grid) and the text box sends `{op:"type"}` over `auoreo-input`.
 */
export function SharedScreen({
  stream,
  control,
  onInput,
}: {
  stream: MediaStream | null;
  control: boolean;
  onInput: (e: InputEvent) => boolean;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [text, setText] = useState("");
  const [last, setLast] = useState<string | null>(null);
  useEffect(() => {
    if (video.current) video.current.srcObject = stream;
  }, [stream]);
  if (!stream) return null;
  const send = (e: InputEvent, label: string) => setLast(onInput(e) ? label : "input channel not open");
  const type = (e: FormEvent) => {
    e.preventDefault();
    if (!text) return;
    send({ op: "type", text }, `typed ${text.length} chars`);
    setText("");
  };
  return (
    <div className="space-y-1">
      <video
        ref={video}
        autoPlay
        muted
        playsInline
        data-testid="shared-view"
        aria-label={control ? "Shared screen — click to control" : "Local video preview (view only)"}
        className={control ? "w-full max-w-md cursor-crosshair rounded-md border" : "w-full max-w-md rounded-md border"}
        onClick={(e) => {
          if (!control) return;
          const { x, y } = relCoords(e.clientX, e.clientY, e.currentTarget.getBoundingClientRect());
          send({ op: "click", x, y }, `click ${x},${y}`);
        }}
      />
      {control && (
        <form onSubmit={type} className="flex max-w-md gap-1">
          <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type on the remote screen…" aria-label="Type on the remote screen" />
          <Button type="submit" size="sm" variant="outline" disabled={!text}>
            <KeyboardIcon aria-hidden /> Send
          </Button>
        </form>
      )}
      {last && (
        <p className="text-[11px] text-muted-foreground" data-testid="input-last">
          {last}
        </p>
      )}
    </div>
  );
}
