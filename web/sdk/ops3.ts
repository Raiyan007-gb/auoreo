/**
 * G5b surface of the client: WebRTC signaling for the browser call client and the session
 * context gauge. Spread into `createClient()`; every wire type aliases `types.gen.ts`.
 */
import type { components } from "./types.gen";

type S = components["schemas"];
export type RtcCapabilities = S["RtcCapabilities"];
export type RtcIceConfig = S["RtcIceConfig"];
export type RtcIceServer = S["RtcIceServer"];
export type RtcOfferBody = S["RtcOffer"];
export type RtcAnswer = S["RtcAnswer"];
export type RtcClosed = S["RtcClosed"];
export type RtcPeerInfo = S["RtcPeerInfo"];
export type SessionContext = S["SessionContext"];
export type LiveKitTokenRequest = S["LiveKitTokenRequest"];
export type LiveKitToken = S["LiveKitToken"];

type Ops3Request = <T>(method: string, path: string, init?: { body?: unknown }) => Promise<T>;

const enc = encodeURIComponent;

export function g5bMethods(request: Ops3Request) {
  return {
    rtcCapabilities: () => request<RtcCapabilities>("GET", "/api/rtc/capabilities"),
    /** ICE servers (+ ephemeral TURN credentials): fetch right before each offer, never cache. */
    rtcIce: () => request<RtcIceConfig>("GET", "/api/rtc/ice"),
    /** Non-trickle: send the offer after ICE gathering completed. 403 for `input` without teleop. */
    rtcOffer: (body: Omit<RtcOfferBody, "type" | "input"> & { input?: boolean }) =>
      request<RtcAnswer>("POST", "/api/rtc/offer", { body: { type: "offer", input: false, ...body } satisfies RtcOfferBody }),
    rtcClose: (rtcId: string) => request<RtcClosed>("DELETE", `/api/rtc/${enc(rtcId)}`),
    rtcPeers: () => request<RtcPeerInfo[]>("GET", "/api/rtc"),
    /** I8: server-minted LiveKit room token (the API secret never reaches the browser). */
    livekitToken: (body: Partial<LiveKitTokenRequest>) =>
      request<LiveKitToken>("POST", "/api/livekit/token", { body }),
    sessionContext: (sessionId: string) => request<SessionContext>("GET", `/api/sessions/${enc(sessionId)}/context`),
    /** Many sessions in one call (max 100; unknown ids are left out). */
    sessionsContext: (sessionIds: readonly string[]) =>
      request<SessionContext[]>("GET", `/api/context?${sessionIds.map((id) => `session_id=${enc(id)}`).join("&")}`),
  };
}
