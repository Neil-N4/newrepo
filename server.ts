import { $ } from "bun";

const PORT = 3000;
const ROOT = process.cwd();
const PROFILES_DIR = `${ROOT}/data/profiles`;
const OAUTH_DIR = `${ROOT}/data/oauth`;
const PREFERENCES_PATH = `${ROOT}/voice-right.md`;
const GOOGLE_CLIENT_ID = process.env.GOOGLE_CLIENT_ID || "";
const GOOGLE_CLIENT_SECRET = process.env.GOOGLE_CLIENT_SECRET || "";
const GOOGLE_REDIRECT_URI = process.env.GOOGLE_REDIRECT_URI || `http://localhost:${PORT}/oauth/google/callback`;
const GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
    "openid",
    "email",
    "profile",
];
const home = process.env.HOME || "";
const pythonCandidates = [
    process.env.VOICE_RIGHT_PYTHON,
    `${ROOT}/.venv/bin/python`,
    `${ROOT}/../cactus/venv/bin/python`,
    `${home}/cactus/venv/bin/python`,
    `${home}/Documents/cactus/venv/bin/python`,
    `${home}/Documents/Playground/cactus/venv/bin/python`,
    "python3",
].filter(Boolean) as string[];

let PYTHON_BIN = "python3";
for (const candidate of pythonCandidates) {
    if (candidate === "python3") {
        PYTHON_BIN = candidate;
        break;
    }
    if (await Bun.file(candidate).exists()) {
        PYTHON_BIN = candidate;
        break;
    }
}

function slugify(name: string) {
    return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "default";
}

function isValidProfileListItem(item: any) {
    if (!item || typeof item !== "object") return false;
    const rawName = typeof item.name === "string" ? item.name.trim() : "";
    const rawId = typeof item.id === "string" ? item.id.trim() : "";
    const lowered = rawName.toLowerCase();
    if (!rawName || rawName === "[object Object]") return false;
    if (!rawId || rawId === "object-object") return false;
    if (lowered === "object" || lowered.includes("[object object]")) return false;
    if (rawId === "default") return true;
    const hasSignal =
        Number(item.terms || 0) > 0 ||
        Number(item.sources || 0) > 0 ||
        Number(item.people || 0) > 0 ||
        Number(item.corrections || 0) > 0;
    if (!hasSignal) return false;
    return true;
}

function profilePath(nameOrId: string) {
    return `${PROFILES_DIR}/${slugify(nameOrId)}.voicepassport.json`;
}

function gmailTokenPath(nameOrId: string) {
    return `${OAUTH_DIR}/${slugify(nameOrId)}.gmail.json`;
}

async function ensureProfilesDir() {
    await Bun.$`mkdir -p ${PROFILES_DIR}`.quiet();
}

async function ensureOauthDir() {
    await Bun.$`mkdir -p ${OAUTH_DIR}`.quiet();
}

async function ensureDefaultProfile() {
    await ensureProfilesDir();
    await ensureOauthDir();
    const path = profilePath("default");
    if (!(await Bun.file(path).exists())) {
        await runBrain(["create-profile", "Profile"], path);
    }
}

async function runBrain(args: string[], profile?: string) {
    let cmd = $`${PYTHON_BIN} brain.py ${args}`;
    if (profile) cmd = cmd.env({ VOICE_RIGHT_PROFILE_PATH: profile });
    return await cmd.quiet().text();
}

async function runStt(args: string[], profile?: string) {
    let cmd = $`${PYTHON_BIN} stt.py ${args}`;
    if (profile) cmd = cmd.env({ VOICE_RIGHT_PROFILE_PATH: profile });
    return await cmd.quiet().text();
}

async function runComposePayload(payload: unknown) {
    const tmpPath = `/tmp/voice-right-compose-payload-${Date.now()}-${Math.random().toString(36).slice(2)}.json`;
    await Bun.write(tmpPath, JSON.stringify(payload, null, 2));
    try {
        return await $`${PYTHON_BIN} compose_route.py ${tmpPath}`.quiet().text();
    } finally {
        await Bun.$`rm -f ${tmpPath}`.quiet();
    }
}

async function runActionPayload(payload: unknown) {
    const tmpPath = `/tmp/voice-right-action-payload-${Date.now()}-${Math.random().toString(36).slice(2)}.json`;
    await Bun.write(tmpPath, JSON.stringify(payload, null, 2));
    try {
        return await $`${PYTHON_BIN} actions.py ${tmpPath}`.quiet().text();
    } finally {
        await Bun.$`rm -f ${tmpPath}`.quiet();
    }
}

async function loadProfile(id = "default") {
    await ensureDefaultProfile();
    const path = profilePath(id);
    const file = Bun.file(path);
    if (!(await file.exists())) {
        await runBrain(["create-profile", id], path);
    }
    return await Bun.file(path).json();
}

async function saveProfile(id: string, profile: unknown) {
    await ensureProfilesDir();
    await Bun.write(profilePath(id), JSON.stringify(profile, null, 2));
}

function parseLastJson(raw: string) {
    const lines = raw.trim().split("\n").filter(Boolean);
    for (let i = lines.length - 1; i >= 0; i--) {
        try {
            return JSON.parse(lines[i]);
        } catch {
            continue;
        }
    }
    return null;
}

function base64UrlDecode(input: string) {
    const normalized = input.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized + "=".repeat((4 - (normalized.length % 4 || 4)) % 4);
    return Buffer.from(padded, "base64").toString("utf8");
}

async function loadGmailTokens(profileId: string) {
    const file = Bun.file(gmailTokenPath(profileId));
    if (!(await file.exists())) return null;
    return await file.json();
}

async function saveGmailTokens(profileId: string, tokens: unknown) {
    await ensureOauthDir();
    await Bun.write(gmailTokenPath(profileId), JSON.stringify(tokens, null, 2));
}

async function deleteGmailTokens(profileId: string) {
    const path = gmailTokenPath(profileId);
    if (await Bun.file(path).exists()) await Bun.$`rm -f ${path}`.quiet();
}

function oauthConfigured() {
    return Boolean(GOOGLE_CLIENT_ID && GOOGLE_CLIENT_SECRET && GOOGLE_REDIRECT_URI);
}

const oauthStates = new Map<string, { profileId: string; createdAt: number }>();

function gmailAuthUrl(profileId: string) {
    const state = crypto.randomUUID();
    oauthStates.set(state, { profileId, createdAt: Date.now() });
    const params = new URLSearchParams({
        client_id: GOOGLE_CLIENT_ID,
        redirect_uri: GOOGLE_REDIRECT_URI,
        response_type: "code",
        access_type: "offline",
        prompt: "consent",
        scope: GOOGLE_SCOPES.join(" "),
        state,
    });
    return `https://accounts.google.com/o/oauth2/v2/auth?${params.toString()}`;
}

async function exchangeGoogleCode(code: string) {
    const params = new URLSearchParams({
        code,
        client_id: GOOGLE_CLIENT_ID,
        client_secret: GOOGLE_CLIENT_SECRET,
        redirect_uri: GOOGLE_REDIRECT_URI,
        grant_type: "authorization_code",
    });
    const res = await fetch("https://oauth2.googleapis.com/token", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: params.toString(),
    });
    if (!res.ok) throw new Error(`Google token exchange failed (${res.status})`);
    return await res.json();
}

async function refreshGoogleToken(profileId: string, tokenPayload: any) {
    if (!tokenPayload?.refresh_token) return tokenPayload;
    const expiresAt = Number(tokenPayload.expires_at || 0);
    if (expiresAt && expiresAt > Date.now() + 30_000) return tokenPayload;

    const params = new URLSearchParams({
        client_id: GOOGLE_CLIENT_ID,
        client_secret: GOOGLE_CLIENT_SECRET,
        refresh_token: tokenPayload.refresh_token,
        grant_type: "refresh_token",
    });
    const res = await fetch("https://oauth2.googleapis.com/token", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: params.toString(),
    });
    if (!res.ok) throw new Error(`Google token refresh failed (${res.status})`);
    const refreshed = await res.json();
    const merged = {
        ...tokenPayload,
        ...refreshed,
        expires_at: Date.now() + Number(refreshed.expires_in || 3600) * 1000,
    };
    await saveGmailTokens(profileId, merged);
    return merged;
}

async function gmailApi<T>(profileId: string, path: string) {
    const tokenPayload = await loadGmailTokens(profileId);
    if (!tokenPayload) throw new Error("Gmail is not connected for this profile");
    const tokens = await refreshGoogleToken(profileId, tokenPayload);
    const res = await fetch(`https://gmail.googleapis.com/gmail/v1/${path}`, {
        headers: { Authorization: `Bearer ${tokens.access_token}` },
    });
    if (!res.ok) throw new Error(`Gmail API failed (${res.status})`);
    return (await res.json()) as T;
}

async function gmailProfile(profileId: string) {
    return await gmailApi<{ emailAddress: string; messagesTotal: number; threadsTotal: number }>(profileId, "users/me/profile");
}

function extractPlainBody(payload: any): string {
    if (!payload) return "";
    if (payload.mimeType === "text/plain" && payload.body?.data) {
        return base64UrlDecode(payload.body.data);
    }
    if (Array.isArray(payload.parts)) {
        for (const part of payload.parts) {
            const text = extractPlainBody(part);
            if (text) return text;
        }
    }
    return "";
}

async function importGmailSentMail(profileId: string, maxResults = 15) {
    const listing = await gmailApi<{ messages?: Array<{ id: string }> }>(
        profileId,
        `users/me/messages?q=${encodeURIComponent("in:sent -in:chats newer_than:365d")}&maxResults=${maxResults}`,
    );
    const ids = listing.messages || [];
    let combined = "";
    for (const item of ids) {
        const message = await gmailApi<any>(profileId, `users/me/messages/${item.id}?format=full`);
        const headers = Object.fromEntries((message.payload?.headers || []).map((h: any) => [String(h.name).toLowerCase(), h.value]));
        const body = extractPlainBody(message.payload) || message.snippet || "";
        combined += [
            `Subject: ${headers.subject || ""}`,
            `To: ${headers.to || ""}`,
            `Date: ${headers.date || ""}`,
            body.trim(),
            "",
        ].join("\n");
    }
    return combined.trim();
}

function isTextLike(file: File) {
    const name = file.name.toLowerCase();
    return (
        file.type.startsWith("text/") ||
        [".md", ".txt", ".json", ".csv", ".tsv", ".html", ".xml", ".eml"].some((ext) => name.endsWith(ext))
    );
}

async function extractText(file: File, tmpPath: string) {
    if (isTextLike(file)) {
        return await file.text();
    }

    const lower = file.name.toLowerCase();
    if ([".rtf", ".doc", ".docx", ".odt", ".html"].some((ext) => lower.endsWith(ext))) {
        try {
            return (await $`textutil -convert txt -stdout ${tmpPath}`.quiet().text()).trim();
        } catch {
            return "";
        }
    }

    if (lower.endsWith(".pdf")) {
        try {
            return (await $`strings ${tmpPath}`.quiet().text()).slice(0, 12000).trim();
        } catch {
            return "";
        }
    }

    return "";
}

function normaliseTargets(input: FormDataEntryValue | null) {
    const raw = String(input || "");
    return raw
        .split(",")
        .map((item) => item.trim().toLowerCase())
        .filter(Boolean);
}

await ensureDefaultProfile();

Bun.serve({
    port: PORT,
    routes: {
        "/": async () =>
            new Response(Bun.file("index.html"), {
                headers: {
                    "Cache-Control": "no-store, no-cache, must-revalidate",
                },
            }),

        "/qr": async (req) => {
            const host = req.headers.get("host") || `localhost:${PORT}`;
            const proto = host.startsWith("localhost") || host.startsWith("127.") ? "http" : "https";
            const targetUrl = `${proto}://${host}/`;
            const qrImg = `https://api.qrserver.com/v1/create-qr-code/?size=420x420&margin=20&bgcolor=09090b&color=fafafa&data=${encodeURIComponent(targetUrl)}`;
            return new Response(
                `<!doctype html><html><body style="background:#09090b;color:#fafafa;font-family:Inter,system-ui;display:grid;place-items:center;min-height:100vh">
                <div style="text-align:center"><h1>Voice Right</h1><p>Scan to try it live.</p><img src="${qrImg}" style="border-radius:20px"><p>${targetUrl}</p></div>
                </body></html>`,
                { headers: { "Content-Type": "text/html; charset=utf-8" } },
            );
        },

        "/api/profiles": {
            async GET() {
                const raw = await runBrain(["profiles"]);
                const parsed = parseLastJson(raw);
                const list = Array.isArray(parsed) ? parsed.filter(isValidProfileListItem) : [];
                return Response.json(list);
            },
            async POST(req) {
                const body = (await req.json()) as { name?: string };
                const name = body.name?.trim();
                if (!name) return Response.json({ error: "name required" }, { status: 400 });
                const path = profilePath(name);
                const raw = await runBrain(["create-profile", name], path);
                return Response.json(parseLastJson(raw) || {});
            },
        },

        "/api/profile": {
            async GET(req) {
                const id = new URL(req.url).searchParams.get("id") || "default";
                return Response.json(await loadProfile(id));
            },
            async PUT(req) {
                const id = new URL(req.url).searchParams.get("id") || "default";
                await saveProfile(id, await req.json());
                return Response.json({ ok: true });
            },
        },

        "/api/preferences": async () => {
            const text = (await Bun.file(PREFERENCES_PATH).exists()) ? await Bun.file(PREFERENCES_PATH).text() : "";
            return Response.json({ markdown: text });
        },

        "/api/gmail/status": {
            async GET(req) {
                const id = new URL(req.url).searchParams.get("profile") || "default";
                if (!oauthConfigured()) {
                    return Response.json({ connected: false, configured: false });
                }
                const tokens = await loadGmailTokens(id);
                if (!tokens) return Response.json({ connected: false, configured: true });
                try {
                    const account = await gmailProfile(id);
                    return Response.json({ connected: true, configured: true, email: account.emailAddress });
                } catch {
                    return Response.json({ connected: true, configured: true, stale: true });
                }
            },
        },

        "/api/gmail/connect": {
            async GET(req) {
                const id = new URL(req.url).searchParams.get("profile") || "default";
                if (!oauthConfigured()) {
                    return Response.json({ error: "Google OAuth is not configured" }, { status: 400 });
                }
                return Response.redirect(gmailAuthUrl(id), 302);
            },
        },

        "/oauth/google/callback": {
            async GET(req) {
                const url = new URL(req.url);
                const code = url.searchParams.get("code");
                const state = url.searchParams.get("state");
                const error = url.searchParams.get("error");
                if (error) {
                    return Response.redirect(`/?gmail=${encodeURIComponent(error)}`, 302);
                }
                if (!code || !state || !oauthStates.has(state)) {
                    return Response.redirect("/?gmail=invalid_oauth_state", 302);
                }
                const { profileId } = oauthStates.get(state)!;
                oauthStates.delete(state);
                try {
                    const tokens: any = await exchangeGoogleCode(code);
                    const enriched = {
                        ...tokens,
                        expires_at: Date.now() + Number(tokens.expires_in || 3600) * 1000,
                    };
                    await saveGmailTokens(profileId, enriched);
                    const gmail = await gmailProfile(profileId);
                    const profile = await loadProfile(profileId);
                    const linked = Array.isArray((profile as any).linked_accounts) ? (profile as any).linked_accounts : [];
                    const nextLinked = linked.filter((item: any) => item.provider !== "gmail");
                    nextLinked.push({
                        provider: "gmail",
                        email: gmail.emailAddress,
                        connected_at: new Date().toISOString(),
                    });
                    (profile as any).linked_accounts = nextLinked;
                    await saveProfile(profileId, profile);
                    return Response.redirect(`/?profile=${encodeURIComponent(profileId)}&gmail=connected`, 302);
                } catch (err) {
                    return Response.redirect(`/?profile=${encodeURIComponent(profileId)}&gmail=oauth_failed`, 302);
                }
            },
        },

        "/api/gmail/import": {
            async POST(req) {
                try {
                    const body = (await req.json()) as { profile?: string; maxResults?: number };
                    const id = body.profile || "default";
                    const maxResults = Math.min(Math.max(Number(body.maxResults || 15), 1), 50);
                    const content = await importGmailSentMail(id, maxResults);
                    const raw = await runBrain(["import", "gmail", "gmail-sent-mail", content], profilePath(id));
                    const payload = parseLastJson(raw) || {};
                    return Response.json({ ok: true, imported: true, ...(payload as object) });
                } catch (err) {
                    return Response.json({ error: String(err) }, { status: 500 });
                }
            },
        },

        "/api/gmail/disconnect": {
            async POST(req) {
                const body = (await req.json()) as { profile?: string };
                const id = body.profile || "default";
                await deleteGmailTokens(id);
                const profile = await loadProfile(id);
                (profile as any).linked_accounts = ((profile as any).linked_accounts || []).filter((item: any) => item.provider !== "gmail");
                await saveProfile(id, profile);
                return Response.json({ ok: true });
            },
        },

        "/api/import-memory": {
            async POST(req) {
                try {
                    const form = await req.formData();
                    const id = String(form.get("profile") || "default");
                    const profile = profilePath(id);
                    const file = form.get("file") as File | null;
                    if (!file || typeof file === "string") {
                        return Response.json({ error: "file required" }, { status: 400 });
                    }
                    const ext = (file.name.split(".").pop() || "bin").toLowerCase();
                    const tmpPath = `/tmp/voice-right-import-${Date.now()}.${ext}`;
                    await Bun.write(tmpPath, file);

                    let raw = "";
                    if (file.type.startsWith("image/")) {
                        raw = await runBrain(["import", "image", file.name, tmpPath], profile);
                    } else {
                        const text = await extractText(file, tmpPath);
                        raw = await runBrain(["import", ext || "text", file.name, text || file.name], profile);
                    }
                    const payload = parseLastJson(raw);
                    return Response.json({ ok: true, ...(payload || {}) });
                } catch (err) {
                    return Response.json({ error: String(err) }, { status: 500 });
                }
            },
        },

        "/api/calibrate/generate": {
            async POST(req) {
                const body = (await req.json().catch(() => ({}))) as { profile?: string; terms?: string[] };
                const id = body.profile || "default";
                const profile = profilePath(id);
                const termArg = Array.isArray(body.terms) && body.terms.length ? body.terms.join(",") : "";
                const raw = termArg ? await runBrain(["calibrate", termArg], profile) : await runBrain(["calibrate"], profile);
                return Response.json({ sentences: parseLastJson(raw) || [] });
            },
        },

        "/api/calibrate/benchmark": {
            async POST(req) {
                try {
                    const form = await req.formData();
                    const id = String(form.get("profile") || "default");
                    const profile = profilePath(id);
                    const file = form.get("file") as File | null;
                    const expected = String(form.get("expected") || "");
                    if (!file || typeof file === "string") {
                        return Response.json({ error: "file required" }, { status: 400 });
                    }
                    const tmpPath = `/tmp/voice-right-calibrate-${Date.now()}.wav`;
                    await Bun.write(tmpPath, file);
                    const raw = await runStt(["benchmark", tmpPath, expected], profile);
                    return Response.json(parseLastJson(raw) || {});
                } catch (err) {
                    return Response.json({ error: String(err) }, { status: 500 });
                }
            },
        },

        "/api/compose": {
            async POST(req) {
                try {
                    const contentType = req.headers.get("content-type") || "";
                    let id = "default";
                    let profile: any = null;
                    let targets: string[] = [];
                    let typedText = "";
                    let payload: Record<string, unknown> = {};

                    if (contentType.includes("application/json")) {
                        const body = (await req.json().catch(() => ({}))) as {
                            profile?: string | { id?: string };
                            target_apps?: string[];
                            targets?: string[] | string;
                            transcript?: string;
                            text?: string;
                            audio_b64?: string;
                            confirmed?: boolean;
                            approved?: boolean;
                            execute?: boolean;
                        };
                        id = typeof body.profile === "string" ? body.profile : String(body.profile?.id || "default");
                        profile = await loadProfile(id);
                        targets = Array.isArray(body.target_apps)
                            ? normaliseTargets(body.target_apps.join(","))
                            : normaliseTargets(body.targets ?? "");
                        typedText = String(body.transcript || body.text || "").trim();
                        payload = {
                            transcript: typedText,
                            audio_b64: typeof body.audio_b64 === "string" ? body.audio_b64 : "",
                            profile,
                            target_apps: targets,
                            confirmed: Boolean(body.confirmed),
                            approved: Boolean(body.approved),
                            execute: Boolean(body.execute),
                        };
                    } else {
                        const form = await req.formData();
                        id = String(form.get("profile") || "default");
                        profile = await loadProfile(id);
                        targets = normaliseTargets(form.get("targets"));
                        typedText = String(form.get("text") || "").trim();
                        const file = form.get("file") as File | null;
                        payload = {
                            transcript: typedText,
                            profile,
                            target_apps: targets,
                            confirmed: false,
                            approved: false,
                            execute: false,
                        };

                        if (file && typeof file !== "string" && file.size) {
                            const suffix = (file.name.split(".").pop() || "wav").toLowerCase();
                            const tmpPath = `/tmp/voice-right-compose-${Date.now()}.${suffix}`;
                            await Bun.write(tmpPath, file);
                            payload.file_path = tmpPath;
                        }
                    }

                    if (!typedText && !payload.file_path && !payload.audio_b64) {
                        return Response.json({ error: "audio or text required" }, { status: 400 });
                    }

                    try {
                        const rawCompose = await runComposePayload(payload);
                        const composed = parseLastJson(rawCompose) || {};
                        console.log("[/api/compose] response:", JSON.stringify(composed, null, 2));
                        return Response.json({
                            transcript: composed.transcript || typedText,
                            stt: composed.stt || null,
                            routing: composed.routing || null,
                            action_plan: composed.action_plan || null,
                            execution_result: composed.execution_result || null,
                            actions: composed.actions || [],
                            function_calls: composed.function_calls || [],
                            intent: composed.intent || composed.transcript || typedText,
                            outputs: composed.outputs || {},
                            error: composed.error || null,
                        });
                    } finally {
                        if (payload.file_path) {
                            await Bun.$`rm -f ${String(payload.file_path)}`.quiet();
                        }
                    }
                } catch (err) {
                    return Response.json({ error: String(err) }, { status: 500 });
                }
            },
        },

        "/api/execute-action": {
            async POST(req) {
                try {
                    const body = (await req.json().catch(() => ({}))) as {
                        profile?: string;
                        function_calls?: Array<{ name?: string; arguments?: Record<string, unknown> }>;
                    };
                    const id = String(body.profile || "default");
                    const profile = await loadProfile(id);
                    const calls = Array.isArray(body.function_calls) ? body.function_calls : [];
                    const results = [];
                    for (const call of calls) {
                        if (!call || typeof call !== "object") continue;
                        const args = typeof call.arguments === "object" && call.arguments ? { ...call.arguments, send_now: true } : { send_now: true };
                        const raw = await runActionPayload({
                            function_name: String(call.name || ""),
                            args,
                            profile,
                        });
                        results.push(parseLastJson(raw) || { status: "error", action: String(call.name || ""), detail: "Action execution failed" });
                    }
                    const successful = results.filter((item: any) => String(item?.status || "").toLowerCase() === "success");
                    return Response.json({
                        ok: true,
                        actions: results,
                        execution_result: successful.length
                            ? {
                                status: "executed",
                                title: "Execution complete",
                                detail: successful.map((item: any) => String(item.detail || "")).filter(Boolean).join(" · "),
                            }
                            : {
                                status: "error",
                                title: "Execution failed",
                                detail: results[0]?.detail || "Unknown error",
                            },
                    });
                } catch (err) {
                    return Response.json({ error: String(err) }, { status: 500 });
                }
            },
        },

        "/api/correction": {
            async POST(req) {
                try {
                    const body = (await req.json().catch(() => ({}))) as {
                        original?: string;
                        corrected?: string;
                        profile?: string;
                    };
                    const profileId = String(body.profile || "default");
                    const original = String(body.original || "").trim();
                    const corrected = String(body.corrected || "").trim();
                    if (!original || !corrected) {
                        return Response.json({ error: "original and corrected are required" }, { status: 400 });
                    }
                    const raw = await runBrain(["correction", original, corrected], profilePath(profileId));
                    const learned = parseLastJson(raw) || [];
                    return Response.json({ ok: true, learned });
                } catch (err) {
                    return Response.json({ error: String(err) }, { status: 500 });
                }
            },
        },

        "/api/style": {
            async POST(req) {
                const { profile = "default", text, app } = (await req.json()) as { profile?: string; text: string; app: string };
                const raw = await runBrain(["style", text, app], profilePath(profile));
                return Response.json({ styled: raw.trim(), app });
            },
        },
    },

    fetch() {
        return new Response("Not Found", { status: 404 });
    },

    error(err) {
        console.error(err);
        return new Response(`Server error: ${err.message}`, { status: 500 });
    },
});

console.log(`Voice Right server running at http://localhost:${PORT}`);
