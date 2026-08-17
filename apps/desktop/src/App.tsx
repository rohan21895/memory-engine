import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import onboardingArtwork from "./assets/onboarding-memory-table.jpg";
import {
  cancelScan,
  chooseFolders,
  loadLibrary,
  loadLibraryStats,
  loadPeoplePage,
  loadProxyAsset,
  localAssetUrl,
  startScan,
} from "./api";
import {
  captureLabel,
  durationLabel,
  formatCount,
  friendlyFolderName,
  monthLabel,
  scanPercent,
} from "./format";
import {
  ArchiveIcon,
  CheckIcon,
  FolderIcon,
  HeartIcon,
  PeopleIcon,
  PlayIcon,
  SearchIcon,
} from "./icons";
import type {
  FaceBox,
  LibraryItem,
  LibraryPage,
  LibraryStats,
  PeoplePage,
  ScanUpdate,
} from "./types";
import { GRID_GAP, GRID_OVERSCAN_ROWS, gridMetrics } from "./virtual-grid";

type Screen = "welcome" | "folders" | "scanning" | "library" | "people";

const emptyPage: LibraryPage = { items: [], total: 0, nextCursor: null, hasMore: false };
const emptyPeople: PeoplePage = { people: [], reviewItems: [] };

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
  const [stats, setStats] = useState<LibraryStats | null>(null);
  const [people, setPeople] = useState<PeoplePage>(emptyPeople);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const libraryRequest = useRef(0);

  const refreshStats = useCallback(async () => {
    const next = await loadLibraryStats();
    setStats(next);
    return next;
  }, []);

  const refreshLibrary = useCallback(async (search = "", cursor: string | null = null) => {
    const request = cursor === null ? ++libraryRequest.current : libraryRequest.current;
    const page = await loadLibrary(search, cursor);
    if (request !== libraryRequest.current) return page;
    setLibrary((current) =>
      cursor === null ? page : { ...page, items: [...current.items, ...page.items] },
    );
    return page;
  }, []);

  useEffect(() => {
    void Promise.all([refreshLibrary(), refreshStats()])
      .then(([page, currentStats]) => {
        if (page.items.length > 0 || (currentStats?.mediaCount ?? 0) > 0) setScreen("library");
      })
      .catch(() => {
        // The browser preview and a first launch may not have a query host yet.
      });
  }, [refreshLibrary, refreshStats]);

  useEffect(() => {
    if (screen !== "library") return;
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      void refreshLibrary(query)
        .catch(() => setError("The private library index could not finish that search. Try again in a moment."))
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
      setScreen("library");
      try {
        await Promise.all([refreshLibrary(), refreshStats()]);
      } catch {
        setError("The scan is safely saved. The private library index is still catching up.");
      }
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

  async function showPeople() {
    setScreen("people");
    setLoading(true);
    setError(null);
    try {
      setPeople(await loadPeoplePage());
    } catch {
      setError("People are still being prepared by the private library index.");
    } finally {
      setLoading(false);
    }
  }

  async function loadMoreMemories() {
    if (loading || !library.nextCursor) return;
    setLoading(true);
    setError(null);
    try {
      await refreshLibrary(query, library.nextCursor);
    } catch {
      setError("More memories could not be loaded just now. Your place in the library is saved.");
    } finally {
      setLoading(false);
    }
  }

  if (screen === "library") {
    return (
      <LibraryScreen
        library={library}
        stats={stats}
        query={query}
        loading={loading}
        error={error}
        onQueryChange={setQuery}
        onAddFolders={pickFolders}
        onShowPeople={() => void showPeople()}
        onLoadMore={() => void loadMoreMemories()}
      />
    );
  }

  if (screen === "people") {
    return (
      <PeopleScreen
        page={people}
        loading={loading}
        error={error}
        onShowLibrary={() => setScreen("library")}
        onAddFolders={pickFolders}
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

type RailProps = {
  active: "library" | "people";
  onShowLibrary: () => void;
  onShowPeople: () => void;
  onAddFolders: () => void;
};

function LibraryRail({ active, onShowLibrary, onShowPeople, onAddFolders }: RailProps) {
  return (
    <aside className="library-rail">
      <Brand />
      <nav aria-label="Library sections">
        <button
          className={`rail-link ${active === "library" ? "is-active" : ""}`}
          type="button"
          aria-current={active === "library" ? "page" : undefined}
          onClick={onShowLibrary}
        >
          <ArchiveIcon /> Library
        </button>
        <span className="rail-link is-disabled" aria-disabled="true"><HeartIcon /> Favorites <small>Later</small></span>
        <button
          className={`rail-link ${active === "people" ? "is-active" : ""}`}
          type="button"
          aria-current={active === "people" ? "page" : undefined}
          onClick={onShowPeople}
        >
          <PeopleIcon /> People
        </button>
      </nav>
      <div className="rail-bottom">
        <button type="button" onClick={onAddFolders}><FolderIcon /> Add folders</button>
        <p>Private on this computer</p>
      </div>
    </aside>
  );
}

type LibraryScreenProps = {
  library: LibraryPage;
  stats: LibraryStats | null;
  query: string;
  loading: boolean;
  error: string | null;
  onQueryChange: (value: string) => void;
  onAddFolders: () => void;
  onShowPeople: () => void;
  onLoadMore: () => void;
};

function LibraryScreen({
  library,
  stats,
  query,
  loading,
  error,
  onQueryChange,
  onAddFolders,
  onShowPeople,
  onLoadMore,
}: LibraryScreenProps) {
  const heading = query ? `Results for “${query}”` : "All memories";
  const awaiting = (stats?.mediaAwaitingAnalysis ?? 0) + (stats?.mediaAwaitingProxies ?? 0);
  const totalLabel = library.total < 0 ? "Memories found" : `${formatCount(library.total)} photos and videos`;
  return (
    <main className="library-shell">
      <LibraryRail
        active="library"
        onShowLibrary={() => undefined}
        onShowPeople={onShowPeople}
        onAddFolders={onAddFolders}
      />
      <section className="library-main" id="library">
        <header className="library-header">
          <div>
            <p className="eyebrow">Your family archive</p>
            <h1>{heading}</h1>
          </div>
          <label className="search-box">
            <SearchIcon />
            <span className="search-label">Search your library</span>
            <input
              type="search"
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
              placeholder="Beach, birthday, Delhi…"
            />
            {loading && <span className="searching">Searching your private index…</span>}
          </label>
        </header>

        <div className="library-summary">
          <p>{totalLabel}</p>
          <span>Originals untouched</span>
        </div>
        {awaiting > 0 && (
          <p className="organizing-note" role="status">
            <span className="organizing-pulse" aria-hidden="true" />
            Still organizing {formatCount(awaiting)} {awaiting === 1 ? "item" : "items"}. The library will fill in as they finish.
          </p>
        )}
        {error && <p className="inline-error library-error" role="alert">{error}</p>}

        {library.items.length === 0 ? (
          <div className="library-empty">
            <span className="empty-sun" aria-hidden="true" />
            <h2>{query ? "No memories match that search." : "Your library is ready for its first folder."}</h2>
            <p>{query ? "Try a place, person, or broader word." : "Add a photo folder and Photeo will organize it safely."}</p>
            {!query && <button className="primary-action" type="button" onClick={onAddFolders}>Choose a folder</button>}
          </div>
        ) : (
          <>
            <p className="date-heading">
              {monthLabel(library.items[0]?.capturedUnix ?? null, library.items[0]?.capturePrecision)}
            </p>
            <VirtualMemoryGrid items={library.items} />
            {library.hasMore && library.nextCursor && (
              <button className="load-more" type="button" disabled={loading} onClick={onLoadMore}>
                {loading ? "Bringing in more memories…" : "Show more memories"}
              </button>
            )}
          </>
        )}
      </section>
    </main>
  );
}

function VirtualMemoryGrid({ items }: { items: LibraryItem[] }) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(900);
  useLayoutEffect(() => {
    const element = scrollerRef.current;
    if (!element) return;
    const measure = () => setWidth(element.clientWidth);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const { columns, rowCount, rowHeight } = gridMetrics(width, items.length);
  const virtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => scrollerRef.current,
    estimateSize: () => rowHeight + GRID_GAP,
    overscan: GRID_OVERSCAN_ROWS,
  });
  return (
    <div className="memory-grid-window" ref={scrollerRef} aria-label="Memory grid">
      <div className="memory-grid-spacer" style={{ height: virtualizer.getTotalSize() }}>
        {virtualizer.getVirtualItems().map((row) => (
          <div
            className="memory-grid-row"
            key={row.key}
            style={{
              height: rowHeight,
              gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
              transform: `translateY(${row.start}px)`,
            }}
          >
            {items.slice(row.index * columns, row.index * columns + columns).map((item) => (
              <MemoryTile item={item} key={item.mediaId} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function MemoryTile({ item }: { item: LibraryItem }) {
  const duration = durationLabel(item.durationMs);
  const date = captureLabel(item.capturedUnix, item.capturePrecision);
  return (
    <article className="memory-tile" tabIndex={0}>
      <PrivateProxyImage proxyId={item.thumbnailProxyId} alt={`${item.kind} captured ${date}`} />
      <div className="memory-shade">
        <span>{date}</span>
        {item.kind === "video" && <span className="video-badge"><PlayIcon /> {duration ?? "Video"}</span>}
      </div>
      {item.favorite && <span className="favorite-badge" aria-label="Favorite"><HeartIcon /></span>}
      {item.safetyUnknown && <span className="safety-pending" title="Safety check pending">Checking</span>}
    </article>
  );
}

type PeopleScreenProps = {
  page: PeoplePage;
  loading: boolean;
  error: string | null;
  onShowLibrary: () => void;
  onAddFolders: () => void;
};

function PeopleScreen({ page, loading, error, onShowLibrary, onAddFolders }: PeopleScreenProps) {
  const names = useMemo(
    () => new Map(page.people.map((person) => [person.personId, person.displayName || "Unnamed person"])),
    [page.people],
  );
  return (
    <main className="library-shell">
      <LibraryRail
        active="people"
        onShowLibrary={onShowLibrary}
        onShowPeople={() => undefined}
        onAddFolders={onAddFolders}
      />
      <section className="library-main people-main">
        <header className="people-header">
          <div>
            <p className="eyebrow">Familiar faces</p>
            <h1>People in your memories</h1>
          </div>
          <p>Only confirmed matches appear by name. Uncertain faces stay questions.</p>
        </header>
        {loading && <p className="organizing-note" role="status"><span className="organizing-pulse" /> Finding familiar faces…</p>}
        {error && <p className="inline-error library-error" role="alert">{error}</p>}

        <section className="people-roster" aria-labelledby="confirmed-people">
          <div className="section-heading">
            <h2 id="confirmed-people">Confirmed people</h2>
            <span>{formatCount(page.people.length)}</span>
          </div>
          {page.people.length === 0 ? (
            <p className="quiet-empty">Confirmed people will gather here as face analysis finishes.</p>
          ) : (
            <div className="person-strip">
              {page.people.map((person) => (
                <article className="person-chip" key={person.personId}>
                  <div className="person-portrait">
                    <PrivateProxyImage proxyId={person.coverProxyId} alt="" />
                  </div>
                  <strong>{person.displayName || "Unnamed person"}</strong>
                  <span>{formatCount(person.confirmedFaceCount)} confirmed</span>
                  {person.isMinor && <small>Sharing protected</small>}
                </article>
              ))}
            </div>
          )}
        </section>

        <section className="review-section" aria-labelledby="review-faces">
          <div className="section-heading review-heading">
            <div>
              <p className="eyebrow">Review only for now</p>
              <h2 id="review-faces">Faces that need your eye</h2>
            </div>
            <p>Naming actions stay unavailable until the private consent-aware write path lands.</p>
          </div>
          {page.reviewItems.length === 0 ? (
            <p className="quiet-empty">No uncertain faces need review right now.</p>
          ) : (
            <div className="review-grid">
              {page.reviewItems.map((item) => (
                <article className="review-item" key={item.faceId}>
                  <div className="review-photo">
                    <PrivateProxyImage proxyId={item.proxyId} alt="Face awaiting review" />
                    {item.faceBox && <FaceMarker box={item.faceBox} />}
                  </div>
                  <p>{item.candidatePersonId ? `Could this be ${names.get(item.candidatePersonId) ?? "someone you know"}?` : "Who is this?"}</p>
                  <span>{Math.round(item.similarity * 100)}% visual match · not confirmed</span>
                </article>
              ))}
            </div>
          )}
        </section>
      </section>
    </main>
  );
}

function FaceMarker({ box }: { box: FaceBox }) {
  return (
    <span
      className="face-marker"
      aria-hidden="true"
      style={{ left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.w * 100}%`, height: `${box.h * 100}%` }}
    />
  );
}

function PrivateProxyImage({ proxyId, alt }: { proxyId: string | null; alt: string }) {
  const [source, setSource] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    setFailed(false);
    setSource(null);
    if (!proxyId) {
      setSource(null);
      return () => {
        active = false;
      };
    }
    void loadProxyAsset(proxyId)
      .then((asset) => {
        const url = localAssetUrl(asset.path);
        if (active && url) {
          setSource(url);
        }
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, [proxyId]);

  if (source) return <img src={source} alt={alt} loading="lazy" />;
  return (
    <div className="memory-placeholder" role={failed ? "status" : undefined}>
      <ArchiveIcon />
      <span>{failed ? "Preview unavailable" : proxyId ? "Preparing preview" : "Preview coming soon"}</span>
    </div>
  );
}

export default App;
