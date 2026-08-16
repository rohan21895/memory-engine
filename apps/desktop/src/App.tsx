import { useCallback, useEffect, useMemo, useState } from "react";
import onboardingArtwork from "./assets/onboarding-memory-table.jpg";
import {
  cancelScan,
  chooseFolders,
  loadLibrary,
  localAssetUrl,
  startScan,
} from "./api";
import { formatCount, friendlyFolderName, monthLabel, scanPercent } from "./format";
import {
  ArchiveIcon,
  CheckIcon,
  FolderIcon,
  HeartIcon,
  PeopleIcon,
  PlayIcon,
  SearchIcon,
} from "./icons";
import type { LibraryItem, LibraryPage, ScanUpdate } from "./types";

type Screen = "welcome" | "folders" | "scanning" | "library";

const emptyPage: LibraryPage = { items: [], total: 0, offset: 0, hasMore: false };

function App() {
  const [screen, setScreen] = useState<Screen>("welcome");
  const [roots, setRoots] = useState<string[]>([]);
  const [scan, setScan] = useState<ScanUpdate>({
    phase: "preparing",
    filesDone: 0,
    filesTotal: null,
    quarantined: 0,
    message: "Getting your library ready…",
  });
  const [library, setLibrary] = useState<LibraryPage>(emptyPage);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refreshLibrary = useCallback(async (search = "", offset = 0) => {
    const page = await loadLibrary(search, offset);
    setLibrary((current) =>
      offset === 0 ? page : { ...page, items: [...current.items, ...page.items] },
    );
    return page;
  }, []);

  useEffect(() => {
    void refreshLibrary()
      .then((page) => {
        if (page.total > 0) setScreen("library");
      })
      .catch(() => {
        // The browser preview has no native library. Onboarding remains usable.
      });
  }, [refreshLibrary]);

  useEffect(() => {
    if (screen !== "library") return;
    const timer = window.setTimeout(() => {
      setLoading(true);
      void refreshLibrary(query)
        .catch(() => setError("We couldn’t search your library just now."))
        .finally(() => setLoading(false));
    }, 220);
    return () => window.clearTimeout(timer);
  }, [query, refreshLibrary, screen]);

  async function pickFolders() {
    setError(null);
    try {
      const selected = await chooseFolders();
      if (selected.length > 0) {
        setRoots((current) => [...new Set([...current, ...selected])]);
        setScreen("folders");
      }
    } catch {
      setError("We couldn’t open the folder picker. Please try again.");
    }
  }

  async function beginScan() {
    if (roots.length === 0) return;
    setError(null);
    setScreen("scanning");
    try {
      const summary = await startScan(roots, setScan);
      if (!summary.complete) {
        setScreen("folders");
        return;
      }
      await refreshLibrary();
      setScreen("library");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setScreen("folders");
    }
  }

  async function pauseScan() {
    await cancelScan();
    setScan((current) => ({
      ...current,
      phase: "paused",
      message: "Paused safely. You can continue whenever you’re ready.",
    }));
    setScreen("folders");
  }

  if (screen === "library") {
    return (
      <LibraryScreen
        library={library}
        query={query}
        loading={loading}
        onQueryChange={setQuery}
        onAddFolders={pickFolders}
        onLoadMore={() => void refreshLibrary(query, library.items.length)}
      />
    );
  }

  return (
    <main className="onboarding-shell">
      <section className="onboarding-copy" aria-labelledby="onboarding-title">
        <Brand />
        {screen === "welcome" && (
          <div className="welcome-content entrance-sequence">
            <p className="eyebrow">Your memories, at home</p>
            <h1 id="onboarding-title">Bring every family photo into one calm place.</h1>
            <p className="lead">
              Photeo finds your photos and videos, removes the clutter, and keeps the
              originals exactly where they are.
            </p>
            <button className="primary-action" type="button" onClick={() => void pickFolders()}>
              <FolderIcon />
              Choose photo folders
            </button>
            <p className="button-note">Nothing is uploaded. Nothing is moved.</p>
            <TrustList />
          </div>
        )}

        {screen === "folders" && (
          <div className="folder-step entrance-sequence">
            <button className="back-link" type="button" onClick={() => setScreen("welcome")}>
              Back
            </button>
            <p className="eyebrow">Folders to organize</p>
            <h1 id="onboarding-title">These memories will stay right where they are.</h1>
            <div className="folder-list" aria-label="Selected folders">
              {roots.map((root) => (
                <div className="folder-row" key={root}>
                  <span className="folder-symbol"><FolderIcon /></span>
                  <span>
                    <strong>{friendlyFolderName(root)}</strong>
                    <small>{root}</small>
                  </span>
                  <button
                    type="button"
                    aria-label={`Remove ${friendlyFolderName(root)}`}
                    onClick={() => setRoots((current) => current.filter((item) => item !== root))}
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
            <button className="secondary-action" type="button" onClick={() => void pickFolders()}>
              Add another folder
            </button>
            {error && <p className="inline-error" role="alert">{error}</p>}
            <button
              className="primary-action"
              type="button"
              disabled={roots.length === 0}
              onClick={() => void beginScan()}
            >
              Start organizing
            </button>
            <p className="button-note">You can close Photeo at any time. It will continue later.</p>
          </div>
        )}

        {screen === "scanning" && <ScanProgress update={scan} onPause={pauseScan} />}
      </section>

      <aside className="onboarding-art" aria-label="A family arranging photographs together">
        <img src={onboardingArtwork} alt="Hands arranging family photographs in an album" />
        <div className="art-caption">
          <span>Private by design</span>
          <strong>Your photos never leave this computer.</strong>
        </div>
      </aside>
    </main>
  );
}

function Brand() {
  return (
    <div className="brand" aria-label="Photeo home">
      <span className="brand-mark"><ArchiveIcon /></span>
      <span>Photeo</span>
    </div>
  );
}

function TrustList() {
  return (
    <ul className="trust-list">
      <li><CheckIcon /> Finds duplicates without deleting anything</li>
      <li><CheckIcon /> Remembers progress if you close the app</li>
      <li><CheckIcon /> Works privately on this computer</li>
    </ul>
  );
}

function ScanProgress({ update, onPause }: { update: ScanUpdate; onPause: () => void }) {
  const percent = scanPercent(update.filesDone, update.filesTotal);
  return (
    <div className="scan-step entrance-sequence" aria-live="polite">
      <p className="eyebrow">Organizing your library</p>
      <h1>We’re finding the moments worth keeping close.</h1>
      <div className="scan-orbit" aria-hidden="true">
        <span>{percent === null ? formatCount(update.filesDone) : `${percent}%`}</span>
      </div>
      <p className="scan-message">{update.message}</p>
      <div
        className={`progress-track ${percent === null ? "is-indeterminate" : ""}`}
        role="progressbar"
        aria-label="Library scan progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent ?? undefined}
      >
        <span style={percent === null ? undefined : { transform: `scaleX(${percent / 100})` }} />
      </div>
      <div className="scan-facts">
        <span><strong>{formatCount(update.filesDone)}</strong> files checked</span>
        <span><strong>{formatCount(update.quarantined)}</strong> need attention later</span>
      </div>
      <button className="secondary-action" type="button" onClick={onPause}>Pause safely</button>
      <p className="button-note">Your originals are never changed.</p>
    </div>
  );
}

type LibraryScreenProps = {
  library: LibraryPage;
  query: string;
  loading: boolean;
  onQueryChange: (value: string) => void;
  onAddFolders: () => void;
  onLoadMore: () => void;
};

function LibraryScreen({
  library,
  query,
  loading,
  onQueryChange,
  onAddFolders,
  onLoadMore,
}: LibraryScreenProps) {
  const heading = query ? `Results for “${query}”` : "All memories";
  return (
    <main className="library-shell">
      <aside className="library-rail">
        <Brand />
        <nav aria-label="Library sections">
          <a className="rail-link is-active" href="#library" aria-current="page">
            <ArchiveIcon /> Library
          </a>
          <a className="rail-link" href="#favorites"><HeartIcon /> Favorites</a>
          <span className="rail-link is-disabled" aria-disabled="true">
            <PeopleIcon /> People <small>Later</small>
          </span>
        </nav>
        <div className="rail-bottom">
          <button type="button" onClick={onAddFolders}><FolderIcon /> Add folders</button>
          <p>Private on this computer</p>
        </div>
      </aside>

      <section className="library-main" id="library">
        <header className="library-header">
          <div>
            <p className="eyebrow">Your family archive</p>
            <h1>{heading}</h1>
          </div>
          <label className="search-box">
            <SearchIcon />
            <span className="sr-only">Search your library</span>
            <input
              type="search"
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
              placeholder="Try a filename, place, or moment"
            />
            {loading && <span className="searching">Searching…</span>}
          </label>
        </header>

        <div className="library-summary">
          <p>{formatCount(library.total)} photos and videos</p>
          <span>Originals untouched</span>
        </div>

        {library.items.length === 0 ? (
          <div className="library-empty">
            <span className="empty-sun" aria-hidden="true" />
            <h2>{query ? "No memories match that search." : "Your library is ready for its first folder."}</h2>
            <p>{query ? "Try a filename or a broader word." : "Add a photo folder and Photeo will organize it safely."}</p>
            {!query && <button className="primary-action" type="button" onClick={onAddFolders}>Choose a folder</button>}
          </div>
        ) : (
          <>
            <p className="date-heading">{monthLabel(library.items[0]?.capturedAt ?? null)}</p>
            <div className="memory-grid">
              {library.items.map((item) => <MemoryTile item={item} key={item.mediaId} />)}
            </div>
            {library.hasMore && (
              <button className="load-more" type="button" onClick={onLoadMore}>Show more memories</button>
            )}
          </>
        )}
      </section>
    </main>
  );
}

function MemoryTile({ item }: { item: LibraryItem }) {
  const source = localAssetUrl(item.thumbnailPath);
  const ratio = useMemo(() => {
    if (!item.width || !item.height) return "4 / 3";
    return `${item.width} / ${item.height}`;
  }, [item.height, item.width]);
  return (
    <article className="memory-tile" style={{ aspectRatio: ratio }} tabIndex={0}>
      {source ? (
        <img src={source} alt={item.filename} loading="lazy" />
      ) : (
        <div className="memory-placeholder"><ArchiveIcon /><span>Preview coming soon</span></div>
      )}
      <div className="memory-shade">
        <span>{item.filename}</span>
        {item.kind === "video" && <span className="video-badge"><PlayIcon /> Video</span>}
      </div>
      {item.favorite && <span className="favorite-badge" aria-label="Favorite"><HeartIcon /></span>}
    </article>
  );
}

export default App;
