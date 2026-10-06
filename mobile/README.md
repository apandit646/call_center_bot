# AI Customer Platform — mobile app

An Expo (React Native) app for the same people who use the web portal. It signs in with the same
email and password and talks to the same backend API; the backend needs no changes for it.

## Run it on your phone

1. Install **Expo Go** on the phone (Play Store / App Store).
2. Make sure the backend is running (`docker compose up -d` in the project folder).
3. In this folder:

   ```bash
   npm install --legacy-peer-deps
   npx expo start
   ```

4. Scan the QR code with Expo Go (Android) or the Camera app (iPhone).

If the phone and the PC are on different networks, use `npx expo start --tunnel`.

## Server address

A phone cannot reach `localhost`. The app needs the address where the backend runs **as the phone sees it**:

| Where the phone is | Address to use |
|---|---|
| Anywhere (internet) | The tunnel address, the same as `PUBLIC_BASE_URL`, e.g. `https://xxxx.trycloudflare.com` |
| Same Wi-Fi as the PC | The PC's Wi-Fi address and port 8000, e.g. `192.168.2.119:8000` (find it with `ipconfig`) |

Set it in `.env` as `EXPO_PUBLIC_API_URL` (see `.env.example`; restart `expo start` after changing it), or
type it on the login screen under **Server address**. The login screen remembers it, so when the tunnel
address changes you only tap "Server: … — change".

## What is in the app

| Screen | What you can do |
|---|---|
| Dashboard | Today's tickets, WhatsApp, inbound calls, campaign calls, usage against the plan |
| Tickets | Search and filter; open a ticket; change status, priority, assignee; add a note |
| Chats | WhatsApp conversations; read the chat; reply as a human agent; close it |
| Calls | Inbound calls: who called, outcome, transcript, play the recording, linked ticket |
| More → Campaigns | (admin / supervisor) progress and calls of each campaign; pause, resume, cancel; call transcript, answers, recording |
| More → Customers, Knowledge base, Account | Look up customers; see document status; change password; sign out |
| Platform admin | Companies with usage; suspend / re-activate; change plan limits |

Done on the web portal only: registering a company and its Twilio / Exotel / WhatsApp keys, creating
and scheduling campaigns, uploading knowledge documents, managing users.

## Code layout

```
src/app/          screens (Expo Router: every file is a screen, _layout.tsx files are navigators)
  (tabs)/         the five bottom tabs of the company portal
  platform/       the platform admin's screens
src/api/          API client (login tokens in the phone's secure storage, auto-refresh) and types
src/auth/         who is signed in
src/components/   shared UI (cards, badges, lists that load page by page, recording player)
```

`src/api/types.ts` is a copy of `frontend/src/api/types.ts`; keep the two the same when the API changes.

## Checks

```bash
npx tsc --noEmit     # types
npx expo lint        # lint
npx expo-doctor      # dependency / config health
```

## Building an installable app (APK / App Store)

Use EAS: `npx eas-cli@latest build -p android --profile preview`. A real build only allows `https`
server addresses by default, so use the tunnel or a proper domain, not a plain `http://192.168…` address.
