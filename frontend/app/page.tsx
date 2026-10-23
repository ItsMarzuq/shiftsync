"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "/api";

type Shift = {
  date: string;
  start_time: string;
  end_time: string;
  title: string;
  location?: string | null;
  selected?: boolean;
};

type User = {
  id: number;
  email: string;
  display_name: string;
  employee_name: string;
  default_title: string;
  default_location: string;
  timezone: string;
  google_connected: boolean;
};

type AuthMode = "login" | "register";

async function apiFetch(path: string, init: RequestInit = {}) {
  return fetch(`${API}${path}`, { ...init, credentials: "include" });
}

export default function Home() {
  const [user, setUser] = useState<User | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [authMode, setAuthMode] = useState<AuthMode>("login");
  const [authEmail, setAuthEmail] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authName, setAuthName] = useState("");

  const [file, setFile] = useState<File | null>(null);
  const [shifts, setShifts] = useState<Shift[]>([]);
  const [loading, setLoading] = useState(false);
  const [savingProfile, setSavingProfile] = useState(false);
  const [message, setMessage] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);

  const selected = useMemo(() => shifts.filter((s) => s.selected !== false), [shifts]);

  async function loadMe() {
    try {
      const res = await apiFetch("/me");
      if (res.ok) {
        setUser(await res.json());
      } else {
        setUser(null);
      }
    } finally {
      setAuthChecked(true);
    }
  }

  useEffect(() => {
    loadMe();
  }, []);

  async function submitAuth(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setMessage("");
    try {
      const body =
        authMode === "register"
          ? { email: authEmail, password: authPassword, display_name: authName }
          : { email: authEmail, password: authPassword };
      const res = await apiFetch(`/auth/${authMode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        setMessage(data.detail ?? "Could not sign in");
        return;
      }
      setUser(data);
      setAuthPassword("");
      setMessage(authMode === "register" ? "Account created." : "Signed in.");
    } catch {
      setMessage("Could not reach the ShiftSync backend.");
    } finally {
      setLoading(false);
    }
  }

  async function logout() {
    await apiFetch("/auth/logout", { method: "POST" });
    setUser(null);
    setShifts([]);
    setSettingsOpen(false);
    setMessage("");
  }

  async function saveProfile() {
    if (!user) return;
    setSavingProfile(true);
    setMessage("");
    try {
      const res = await apiFetch("/me", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: user.display_name,
          employee_name: user.employee_name,
          default_title: user.default_title,
          default_location: user.default_location,
          timezone: user.timezone,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setMessage(data.detail ?? "Could not save profile");
        return;
      }
      setUser(data);
      setMessage("Preferences saved to your account.");
    } finally {
      setSavingProfile(false);
    }
  }

  async function parseRoster() {
    if (!file || !user) return;
    setLoading(true);
    setMessage("");
    const body = new FormData();
    body.append("file", file);
    body.append("employee_name", user.employee_name.trim());

    try {
      const res = await apiFetch("/parse-roster", { method: "POST", body });
      const data = await res.json();
      if (!res.ok) {
        setMessage(data.detail ?? "Could not parse roster");
        return;
      }
      setShifts(
        data.shifts.map((shift: Shift) => ({
          ...shift,
          title: user.default_title.trim() || shift.title || "Work Shift",
          location: user.default_location.trim() || shift.location || "",
          selected: true,
        })),
      );
      setMessage(`Found ${data.shifts.length} shift(s) for ${user.employee_name}.`);
    } catch {
      setMessage("Could not reach the ShiftSync backend.");
    } finally {
      setLoading(false);
    }
  }

  async function addToCalendar() {
    setLoading(true);
    setMessage("");
    try {
      const res = await apiFetch("/calendar/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ shifts: selected }),
      });
      const data = await res.json();
      if (!res.ok) {
        setMessage(data.detail ?? "Could not add events");
        return;
      }
      const created = data.created?.length ?? 0;
      const skipped = data.skipped?.length ?? 0;
      setMessage(
        skipped
          ? `Added ${created} shift(s). Skipped ${skipped} duplicate shift(s).`
          : `Added ${created} shift(s) to Google Calendar.`,
      );
    } finally {
      setLoading(false);
    }
  }

  function patchShift(index: number, patch: Partial<Shift>) {
    setShifts((current) => current.map((s, i) => (i === index ? { ...s, ...patch } : s)));
  }

  if (!authChecked) {
    return (
      <main className="authShell">
        <div className="loadingMark">ShiftSync</div>
      </main>
    );
  }

  if (!user) {
    return (
      <main className="authShell">
        <section className="authCard">
          <div className="brandLockup">
            <div className="appIcon">S</div>
            <div>
              <p className="eyebrow">ShiftSync</p>
              <h1 className="authTitle">Your roster, already on your calendar.</h1>
            </div>
          </div>
          <p className="authSubtitle">
            Upload a roster, detect your shifts and sync them to Google Calendar from your phone or computer.
          </p>

          <div className="authTabs" role="tablist">
            <button className={authMode === "login" ? "active" : ""} onClick={() => setAuthMode("login")}>
              Sign in
            </button>
            <button className={authMode === "register" ? "active" : ""} onClick={() => setAuthMode("register")}>
              Create account
            </button>
          </div>

          <form className="authForm" onSubmit={submitAuth}>
            {authMode === "register" && (
              <label className="field">
                <span>Your name</span>
                <input value={authName} onChange={(e) => setAuthName(e.target.value)} autoComplete="name" required />
              </label>
            )}
            <label className="field">
              <span>Email</span>
              <input
                type="email"
                value={authEmail}
                onChange={(e) => setAuthEmail(e.target.value)}
                autoComplete="email"
                required
              />
            </label>
            <label className="field">
              <span>Password</span>
              <input
                type="password"
                minLength={8}
                value={authPassword}
                onChange={(e) => setAuthPassword(e.target.value)}
                autoComplete={authMode === "register" ? "new-password" : "current-password"}
                required
              />
            </label>
            <button className="primary wide" disabled={loading}>
              {loading ? "Working…" : authMode === "register" ? "Create account" : "Sign in"}
            </button>
          </form>
          {message && <p className="message compact">{message}</p>}
          <p className="authFootnote">Installable on Android as a web app. Your saved preferences follow your account.</p>
        </section>
      </main>
    );
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div className="miniBrand">
          <div className="appIcon small">S</div>
          <strong>ShiftSync</strong>
        </div>
        <button className="iconButton" aria-label="Open account settings" onClick={() => setSettingsOpen((v) => !v)}>
          {user.display_name?.trim()?.[0]?.toUpperCase() || user.email[0].toUpperCase()}
        </button>
      </header>

      <section className="hero">
        <div>
          <p className="eyebrow">Roster → Calendar</p>
          <h1>Hi{user.display_name ? `, ${user.display_name.split(" ")[0]}` : ""}.</h1>
          <p className="subtitle">Upload your latest roster. ShiftSync will find your row and prepare the shifts for you.</p>
        </div>
        <button
          className={user.google_connected ? "statusButton connected" : "statusButton"}
          onClick={() => (window.location.href = `${API}/google/login`)}
        >
          <span className="statusDot" />
          {user.google_connected ? "Calendar connected" : "Connect Google Calendar"}
        </button>
      </section>

      {settingsOpen && (
        <section className="card settingsPanel">
          <div className="sectionHeader">
            <div>
              <p className="eyebrow">Account</p>
              <h2>Saved preferences</h2>
            </div>
            <button className="textButton" onClick={logout}>Sign out</button>
          </div>
          <div className="settingsGrid">
            <label className="field">
              <span>Display name</span>
              <input value={user.display_name} onChange={(e) => setUser({ ...user, display_name: e.target.value })} />
            </label>
            <label className="field">
              <span>Name shown on roster</span>
              <input value={user.employee_name} onChange={(e) => setUser({ ...user, employee_name: e.target.value })} />
            </label>
            <label className="field">
              <span>Default shift title</span>
              <input value={user.default_title} onChange={(e) => setUser({ ...user, default_title: e.target.value })} />
            </label>
            <label className="field">
              <span>Default workplace / location</span>
              <input value={user.default_location} onChange={(e) => setUser({ ...user, default_location: e.target.value })} />
            </label>
            <label className="field">
              <span>Timezone</span>
              <input value={user.timezone} onChange={(e) => setUser({ ...user, timezone: e.target.value })} placeholder="Europe/Dublin" />
            </label>
          </div>
          <div className="settingsActions">
            <button className="primary" onClick={saveProfile} disabled={savingProfile}>
              {savingProfile ? "Saving…" : "Save preferences"}
            </button>
            <span className="savedHint">Signed in as {user.email}</span>
          </div>
        </section>
      )}

      <section className="card uploadPanel">
        <div className="uploadCopy">
          <p className="eyebrow">1 · Upload roster</p>
          <h2>Find my shifts</h2>
          <p>Images, Excel, CSV and text-based PDFs are supported.</p>
        </div>
        <label className="dropField">
          <input
            type="file"
            accept=".csv,.xlsx,.xls,.pdf,.png,.jpg,.jpeg,.webp"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <span className="uploadGlyph">↑</span>
          <strong>{file ? file.name : "Choose your roster"}</strong>
          <small>{file ? "Tap to replace" : "Tap to browse files or photos"}</small>
        </label>
        <button className="primary wide" onClick={parseRoster} disabled={!file || !user.employee_name.trim() || loading}>
          {loading ? "Reading roster…" : "Detect my shifts"}
        </button>
        {!user.employee_name.trim() && <p className="hint">Open account settings and enter the name shown on your roster first.</p>}
      </section>

      {message && <p className="message">{message}</p>}

      {shifts.length > 0 && (
        <section className="card reviewCard">
          <div className="sectionHeader">
            <div>
              <p className="eyebrow">2 · Review</p>
              <h2>Detected shifts</h2>
            </div>
            <span className="countPill">{selected.length} selected</span>
          </div>

          <div className="shiftList">
            {shifts.map((shift, index) => (
              <article className={shift.selected === false ? "shiftCard deselected" : "shiftCard"} key={`${shift.date}-${shift.start_time}-${index}`}>
                <div className="shiftSelect">
                  <input
                    className="check"
                    type="checkbox"
                    checked={shift.selected !== false}
                    onChange={(e) => patchShift(index, { selected: e.target.checked })}
                  />
                  <strong>{new Date(`${shift.date}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}</strong>
                </div>
                <label className="miniField dateField"><span>Date</span><input type="date" value={shift.date} onChange={(e) => patchShift(index, { date: e.target.value })} /></label>
                <div className="timePair">
                  <label className="miniField"><span>Start</span><input type="time" value={shift.start_time} onChange={(e) => patchShift(index, { start_time: e.target.value })} /></label>
                  <label className="miniField"><span>End</span><input type="time" value={shift.end_time} onChange={(e) => patchShift(index, { end_time: e.target.value })} /></label>
                </div>
                <label className="miniField"><span>Title</span><input value={shift.title} onChange={(e) => patchShift(index, { title: e.target.value })} /></label>
                <label className="miniField locationField"><span>Location</span><input value={shift.location ?? ""} onChange={(e) => patchShift(index, { location: e.target.value })} /></label>
              </article>
            ))}
          </div>

          <div className="stickyAction">
            <button className="primary wide" disabled={!user.google_connected || selected.length === 0 || loading} onClick={addToCalendar}>
              {loading ? "Adding shifts…" : `Add ${selected.length} shift${selected.length === 1 ? "" : "s"} to Calendar`}
            </button>
            {!user.google_connected && <p className="hint centered">Connect Google Calendar first.</p>}
          </div>
        </section>
      )}

      <footer className="footer">ShiftSync · Install this site from Chrome’s menu to use it like an Android app.</footer>
    </main>
  );
}
