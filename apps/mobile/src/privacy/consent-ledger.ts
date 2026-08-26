import * as SecureStore from "expo-secure-store";

const LEDGER_KEY = "photeo.local-consent-ledger.v1";
const MAX_LOCAL_EVENTS = 100;

export type ConsentEvent = {
  id: string;
  occurredAt: string;
  action: "google-photos-import";
  dataClass: "user-selected-google-photos";
  destinations: ["accounts.google.com", "photospicker.googleapis.com"];
  scope: "photospicker.mediaitems.readonly";
};

export async function recordGooglePhotosConsent(): Promise<ConsentEvent> {
  const event: ConsentEvent = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`,
    occurredAt: new Date().toISOString(),
    action: "google-photos-import",
    dataClass: "user-selected-google-photos",
    destinations: ["accounts.google.com", "photospicker.googleapis.com"],
    scope: "photospicker.mediaitems.readonly",
  };

  const existing = await SecureStore.getItemAsync(LEDGER_KEY);
  const parsed = existing ? (JSON.parse(existing) as ConsentEvent[]) : [];
  const next = [...parsed.slice(-(MAX_LOCAL_EVENTS - 1)), event];
  await SecureStore.setItemAsync(LEDGER_KEY, JSON.stringify(next));
  return event;
}
