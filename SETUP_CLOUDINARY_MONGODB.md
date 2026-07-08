# DocMind — Cloudinary + MongoDB Setup & Verification Guide

This guide explains **where to paste your keys**, how the pieces fit together,
and how to **verify** that users, chats, and PDF uploads are actually being
stored.

---

## 1. Where the keys go (TL;DR)

| Value | File | Variable |
| --- | --- | --- |
| MongoDB connection string | `backend/.env` | `MONGODB_URI` |
| MongoDB database name | `backend/.env` | `MONGODB_DB_NAME` |
| Cloudinary cloud name | `backend/.env` | `CLOUDINARY_CLOUD_NAME` |
| Cloudinary API key | `backend/.env` | `CLOUDINARY_API_KEY` |
| Cloudinary API secret | `backend/.env` | `CLOUDINARY_API_SECRET` |
| Backend URL (for Next proxy) | `frontend/my-app/.env` | `BACKEND_URL` |
| Shared API secret (optional) | both `.env` files | `INTERNAL_API_SECRET` |

> All Cloudinary + MongoDB secrets live **only** in `backend/.env`. The browser
> never sees them — the Next.js server proxies requests to FastAPI.

---

## 2. `backend/.env`

Copy the example and fill it in:

```bash
cp backend/.env.example backend/.env
```

```ini
# MongoDB (local Compass instance)
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB_NAME=docmind

# Cloudinary
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_api_key
CLOUDINARY_API_SECRET=your_api_secret

# App
MAX_PDFS_PER_CHAT=4
# Leave empty for local dev, or set the SAME value in both .env files.
INTERNAL_API_SECRET=
```

### Getting your Cloudinary keys

1. Sign in at <https://console.cloudinary.com>.
2. On the **Dashboard** (or **Settings → API Keys**) you'll see the
   **Product Environment Credentials**:
   - **Cloud name** → `CLOUDINARY_CLOUD_NAME`
   - **API Key** → `CLOUDINARY_API_KEY`
   - **API Secret** (click "reveal") → `CLOUDINARY_API_SECRET`
3. Paste each into `backend/.env`.

### Your MongoDB URI (Compass, local)

Since you're running MongoDB locally with Compass, the default is already
correct:

```
MONGODB_URI=mongodb://localhost:27017
```

If Compass shows a different connection string at the top of the app, paste that
exact value instead (e.g. if you set a username/password).

---

## 3. `frontend/my-app/.env`

Your Clerk keys are already here. These lines were added for the backend proxy:

```ini
# FastAPI backend (server-side only)
BACKEND_URL=http://localhost:8000
# Must match INTERNAL_API_SECRET in backend/.env (leave empty to disable in dev)
INTERNAL_API_SECRET=
```

---

## 4. Install & run

**Backend** (from the repo root, with your virtualenv active):

```bash
source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
uvicorn main:app --reload --port 8000
```

On startup you should see logs like:

```
MongoDB connected: db=docmind
Cloudinary configured for cloud_name=your_cloud_name
```

If instead you see `MongoDB is not configured` / `Cloudinary is not configured`,
your `backend/.env` values are missing or the file wasn't loaded.

**Frontend**:

```bash
cd frontend/my-app
npm run dev
```

---

## 5. How it works (the flow)

```
Browser (Clerk-authenticated)
   │  same-origin /api/* calls only
   ▼
Next.js Route Handlers  ── verify Clerk session, add X-User-Id (+ secret)
   │
   ▼
FastAPI backend
   ├── /users/sync            → upsert user in MongoDB (users collection)
   ├── POST /chats            → create a chat (chats collection)
   ├── POST /chats/{id}/pdfs  → upload to Cloudinary + store ids on the chat
   └── DELETE /chats/{id}/pdfs/{public_id} → remove from Cloudinary + chat
```

- **User sync**: On visiting `/chat`, the app calls `/api/user/sync` once. The
  backend upserts by the **Clerk user id**, so signing in repeatedly never
  creates duplicate users.
- **PDF upload**: A chat is created lazily on your **first** PDF upload. Files
  go to Cloudinary under `docmind/<clerk_user_id>/<chat_id>/`, and their
  `public_id` + `asset_id` (private id) + `secure_url` are stored in the chat's
  `pdf[]` array.

---

## 6. Verify MongoDB in Compass

1. Open **MongoDB Compass** and connect to `mongodb://localhost:27017`.
2. Open the **`docmind`** database. You should see two collections:
   - **`users`** — one document per signed-in account:
     ```json
     {
       "_id": "…",
       "clerk_user_id": "user_2ab…",
       "email": "you@example.com",
       "chats": ["<chatId>"],
       "created_at": "…",
       "updated_at": "…"
     }
     ```
   - **`chats`** — one document per chat, with uploaded PDFs:
     ```json
     {
       "_id": "<chatId>",
       "user_id": "user_2ab…",
       "pdf": [
         {
           "public_id": "docmind/user_2ab…/<chatId>/report_x9f2",
           "private_id": "<cloudinary asset_id>",
           "secure_url": "https://res.cloudinary.com/…/report_x9f2.pdf",
           "resource_type": "image",
           "filename": "report.pdf",
           "bytes": 482113,
           "pages": 32
         }
       ],
       "conversation": []
     }
     ```
3. **Quick test**: sign in → the `users` doc appears. Upload a PDF → a `chats`
   doc appears with an entry in `pdf[]`. Remove the PDF in the UI → the entry
   disappears from `pdf[]`.

> Tip: hit the refresh icon in Compass after each action — it doesn't live-update.

---

## 7. Verify Cloudinary

1. Go to <https://console.cloudinary.com> → **Media Library**.
2. Open the **`docmind`** folder → your `<clerk_user_id>` → `<chat_id>`.
3. Your uploaded PDFs appear there. The `secure_url` in MongoDB should open the
   same file.

**Fetching all of a user's files later** is easy because everything is
namespaced/tagged by user:
- Folder prefix: `docmind/<clerk_user_id>/`
- Tags: `user:<clerk_user_id>` and `chat:<chat_id>` (set on every upload)

> **Cloudinary + PDFs note:** PDFs are uploaded with `resource_type="image"` so
> Cloudinary reports the page count. If PDF *delivery* is blocked on your
> account, enable it under **Settings → Security → "PDF and ZIP files delivery"**.
> This does not affect uploading or storing — only public delivery of the raw file.

---

## 8. About the Cloudinary MCP plugin (in Cursor)

The Cloudinary plugins you see in Cursor (`cloudinary-asset-mgmt`,
`cloudinary-analysis`, `cloudinary-env-config`, …) are **agent tools** for
inspecting/managing your Cloudinary account via chat. They are **separate** from
the app's runtime uploads (which use the Cloudinary **Python SDK** in
`backend/services/cloudinary_setup.py`).

They currently require authentication before use — say *"authenticate the
Cloudinary MCP"* and I'll trigger the auth flow, after which I can browse your
Media Library, check delivery settings, or analyze assets for you.

---

## 9. Optional hardening (later)

- Set `INTERNAL_API_SECRET` to the same random string in both `.env` files so
  only the Next.js server can call FastAPI.
- Replace the on-sign-in sync with a **Clerk webhook** (`user.created`) for
  production. It needs a public URL (e.g. ngrok) which is why the on-demand
  upsert is used for local dev.
