#!/usr/bin/env python3
"""Fetch, verify and install the weights that `models/registry.json` names.

WHY THIS EXISTS

`models/weights/` was empty and populated by hand. The YuNet and SCRFD
inference that ran earlier used files somebody downloaded outside the
repository, so "it worked on my machine" was literally true and nothing about
the analysis stack survived a fresh clone. This script is the missing step
between a checkout and a runnable model host.

WHAT IT GUARANTEES, AND WHAT IT DELIBERATELY DOES NOT

It guarantees that a file installed into `models/weights/` is:

  * the bytes served by the artifact URL its config declares,
  * not an error page, an HTML listing or a git-LFS pointer masquerading as a
    model (all three are things the declared sources actually serve today),
  * byte-identical to `weights.blake3` when the config pins one -- and when it
    is not, the file is NOT installed, in agreement with `models/policy/
    load_gate.py` rather than under a second policy invented here.

It does NOT decide whether a model may be loaded. That is the load gate's job
and it applies more rules than integrity (licence, placeholder state, config
pinning, execution providers). This script reports what the gate would say
about what it installed, and reports it per model, but it never pre-empts it.

IT DOES NOT WRITE PINS. When a config has `blake3: null` the digest is measured
and PRINTED for a human to paste. A pin this script generated from whatever it
happened to download at the time certifies nothing: it would say "these bytes
are the bytes I fetched", which is true of any bytes, including a truncated
download or a repository whose maintainer replaced the file. A pin means a
human decided that a specific artifact is the one this product runs. Automating
that would turn the strongest check in the registry into a tautology, so the
digest is output and the config stays unpinned until someone acts.

THE ARTIFACT-URL RULE

`weights.source_url` must point at a FILE for this script to fetch it: the last
path segment ends in a known artifact suffix, or the URL is a Hugging Face
`/resolve/` download URL. A repository landing page is not an artifact URL and
is never downloaded -- the whole failure mode being avoided is saving 300KB of
HTML as `model.onnx`, hashing it, and printing a digest that looks exactly like
a real one. Entries whose declared source publishes no artifact in the declared
format are reported UNAVAILABLE with the reason, which is the honest state: a
provenance link, not a download.

GATED REPOSITORIES

Hugging Face requests carry `Authorization: Bearer $HF_TOKEN` (or
`$HUGGING_FACE_HUB_TOKEN`) when one is set in the environment. On 401/403 the
report distinguishes "no token set" from "token set and refused", because those
need different actions from the operator. The Authorization header is stripped
when a redirect crosses to a different host, so the token is not handed to a
CDN. NOTE: no token was available in the environment this was written in, so
the authenticated path has never been executed -- see docs/model-registry.md.

EXIT CODES -- ALL DISTINCT, BECAUSE A SKIP IS NOT A PASS

  0  SUCCESS       every model in scope is installed and verified against its pin
  3  PARTIAL       nothing is broken, but something is missing: a model was
                   fetched-but-unpinned, or was unavailable (no artifact URL,
                   gated, network refusal). Work remains.
  4  FAILURE       a pin mismatched, a download was not the artifact it claimed
                   to be, an archive member was absent, or the run accomplished
                   nothing at all (empty scope, or everything unavailable).
  5  PRECONDITION  the script could not run: blake3 missing, registry unreadable.

Fetched-but-unpinned is PARTIAL and never SUCCESS on purpose. An unpinned model
is not reproducible, which is the exact problem this script exists to fix, and
letting it share an exit code with a verified fetch would make green mean
nothing. Argparse's usage error (2) stays out of the way of all four.

IDEMPOTENCE

An installed file that matches its pin is not re-downloaded. An installed file
with no pin is not re-downloaded either -- with nothing to compare against, a
re-fetch could only overwrite a file the operator may have vetted, so it is
reported with its current digest and left alone. `--force` re-downloads
regardless.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_ROOT = REPO_ROOT / "models"
REGISTRY_PATH = MODELS_ROOT / "registry.json"

# The load gate is imported, not reimplemented. Running from a checkout without
# installing anything is the normal case for a bootstrap script, so the repo
# root goes on the path first.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from models.policy import load_gate  # noqa: E402

EXIT_SUCCESS = 0
EXIT_PARTIAL = 3
EXIT_FAILURE = 4
EXIT_PRECONDITION = 5

USER_AGENT = "memory-engine-fetch-weights/1 (+models/registry.json)"
CHUNK = 1024 * 1024
DEFAULT_TIMEOUT = 60.0
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# A URL is treated as pointing at a file only if it looks like one. Suffixes are
# matched against the last path segment, lowercased.
ARTIFACT_SUFFIXES = (
    ".onnx",
    ".onnx_data",
    ".ort",
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".gz",
    ".pt",
    ".pth",
    ".bin",
    ".safetensors",
    ".gguf",
    ".mlmodel",
    ".mlpackage",
    ".dat",
    ".pb",
)

LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"

# First byte of an ONNX ModelProto: a protobuf field tag. Field order is not
# fixed by the format, so every plausible top-level tag is allowed. This is a
# smell test that catches HTML, JSON, text and truncation -- not a parser, and
# it is not claimed to be one.
ONNX_LEADING_TAGS = frozenset({0x08, 0x12, 0x1A, 0x22, 0x28, 0x32, 0x3A, 0x42})

VERIFIED = "VERIFIED"
NEEDS_PIN = "NEEDS_PIN"
UNAVAILABLE = "UNAVAILABLE"
FAILED = "FAILED"
SKIPPED = "SKIPPED"


class Precondition(RuntimeError):
    """The script cannot run at all."""


class Unavailable(RuntimeError):
    """The artifact could not be obtained. Not this repository's defect."""


class Corrupt(RuntimeError):
    """Something arrived, and it was not the artifact. Loud failure."""


class Truncated(RuntimeError):
    """Fewer bytes than the server said it would send.

    Retryable, and separate from Corrupt on purpose: a short read is the
    network dropping a connection, not the source publishing the wrong file,
    and the two deserve different reports as well as different handling.
    """


@dataclass(frozen=True)
class Plan:
    """One registry entry, reduced to what a fetch needs."""

    model_id: str
    config_path: Path
    filename: str
    fmt: str
    source_url: str | None
    archive_member: str | None
    pinned_hash: str | None
    rollout_state: str
    install_path: Path


@dataclass
class Result:
    model_id: str
    state: str
    detail: str
    digest: str | None = None
    byte_size: int | None = None
    config_path: Path | None = None
    corroboration: str | None = None
    gate_development: str | None = None
    gate_release: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class Payload:
    """Bytes on disk plus everything measured while they were being written."""

    path: Path
    byte_size: int
    blake3: str
    sha256: str


# --------------------------------------------------------------------------
# registry reading
# --------------------------------------------------------------------------


def read_json(path: Path) -> dict:
    try:
        parsed = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Precondition(f"cannot read {path}: {error}") from error
    if not isinstance(parsed, dict):
        raise Precondition(f"{path} must contain a JSON object")
    return parsed


def build_plans(
    registry: dict,
    models_root: Path,
    weights_dir: Path,
    *,
    only: Sequence[str],
    include_placeholders: bool,
) -> tuple[list[Plan], list[Result]]:
    """Every entry the registry lists, split into in-scope plans and skips."""
    entries = registry.get("entries")
    if not isinstance(entries, list) or not entries:
        raise Precondition("models/registry.json has no entries")

    known = {str(entry.get("model_id")) for entry in entries}
    unknown = [model_id for model_id in only if model_id not in known]
    if unknown:
        raise Precondition(
            "no such model_id in models/registry.json: " + ", ".join(sorted(unknown))
        )

    plans: list[Plan] = []
    skipped: list[Result] = []
    for entry in entries:
        model_id = str(entry.get("model_id"))
        config_path = models_root / str(entry.get("config", ""))
        config = read_json(config_path)
        weights = config.get("weights")
        if not isinstance(weights, dict):
            raise Precondition(f"{config_path} has no weights block")
        rollout = config.get("rollout")
        state = str((rollout or {}).get("state", ""))

        if only and model_id not in only:
            continue
        if state == "placeholder" and not include_placeholders:
            skipped.append(
                Result(
                    model_id=model_id,
                    state=SKIPPED,
                    detail=(
                        "rollout.state is 'placeholder' -- no checkpoint has been "
                        "chosen for this slot, and the load gate refuses "
                        "placeholders in every mode. Use --include-placeholders "
                        "to attempt it anyway."
                    ),
                    config_path=config_path,
                )
            )
            continue

        filename = str(weights.get("filename") or "")
        if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
            raise Precondition(
                f"{config_path}: weights.filename must be a plain file name, got {filename!r}"
            )
        source_url = weights.get("source_url")
        plans.append(
            Plan(
                model_id=model_id,
                config_path=config_path,
                filename=filename,
                fmt=str(weights.get("format") or ""),
                source_url=str(source_url) if isinstance(source_url, str) else None,
                archive_member=(
                    str(weights["archive_member"])
                    if isinstance(weights.get("archive_member"), str)
                    else None
                ),
                pinned_hash=(
                    str(weights["blake3"])
                    if isinstance(weights.get("blake3"), str)
                    else None
                ),
                rollout_state=state,
                install_path=weights_dir / filename,
            )
        )
    return plans, skipped


# --------------------------------------------------------------------------
# hashing
# --------------------------------------------------------------------------


def require_blake3():
    try:
        from blake3 import blake3  # type: ignore
    except ImportError as error:  # pragma: no cover - environment dependent
        raise Precondition(
            "the blake3 package is required to verify weights: pip install blake3"
        ) from error
    return blake3


def digest_file(path: Path) -> Payload:
    blake3 = require_blake3()
    b3 = blake3()
    sha = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK):
            b3.update(chunk)
            sha.update(chunk)
            size += len(chunk)
    return Payload(path=path, byte_size=size, blake3=b3.hexdigest(), sha256=sha.hexdigest())


# --------------------------------------------------------------------------
# integrity, delegated to the load gate
# --------------------------------------------------------------------------


def integrity_refusal(pinned: str | None, actual: str | None, policy: dict) -> str | None:
    """The load gate's verdict on these bytes, or None to install them.

    Everything the gate checks that is not about the file's integrity is
    neutralised here on purpose. Licence state, placeholder state, config
    pinning and available execution providers are all real gates, and they are
    all applied by ml-runtime at load time against the same module -- but they
    are not reasons to refuse to WRITE a file to disk, and a fetcher that
    silently applied them would be a second, divergent policy. What remains is
    exactly the two always-fatal integrity rules: a digest that disagrees with
    its pin, and a pin that was never checked.

    Evaluated in the most permissive mode, so an unpinned entry is not refused
    here -- it is reported as NEEDS_PIN instead, and the release gate still
    refuses to load it.
    """
    candidate = load_gate.Candidate(
        registered=True,
        weights_present=True,
        pinned_hash=pinned,
        actual_hash=actual,
        license_verified=True,
        blocks_commercial_release=False,
        config_valid=True,
        config_present=True,
        pinned_config_digest=None,
        actual_config_digest=None,
        is_placeholder=False,
        available_providers=("onnxruntime_cpu",),
    )
    reason = load_gate.decide_load(candidate, "development", policy)
    if reason in {
        load_gate.UnloadableReason.HASH_MISMATCH,
        load_gate.UnloadableReason.INTEGRITY_UNVERIFIED,
    }:
        return reason
    return None


def gate_verdicts(plan: Plan, digest: str | None, policy: dict) -> tuple[str | None, str | None]:
    """What the real gate says about the installed file, in both modes.

    Reported, never acted on. This is the number that answers "is the stack
    actually loadable now", and it is read from the gate rather than guessed.
    """
    config = read_json(plan.config_path)
    licence = config.get("license") if isinstance(config.get("license"), dict) else {}
    registry = read_json(REGISTRY_PATH)
    pinned_config = next(
        (
            entry.get("config_blake3")
            for entry in registry.get("entries", [])
            if entry.get("model_id") == plan.model_id
        ),
        None,
    )
    blake3 = require_blake3()
    actual_config = blake3(plan.config_path.read_bytes()).hexdigest()
    candidate = load_gate.Candidate(
        registered=True,
        weights_present=plan.install_path.is_file(),
        pinned_hash=plan.pinned_hash,
        actual_hash=digest,
        license_verified=licence.get("verified") is True,
        blocks_commercial_release=licence.get("blocks_commercial_release") is True,
        config_valid=True,
        config_present=plan.config_path.is_file(),
        pinned_config_digest=pinned_config,
        actual_config_digest=actual_config,
        is_placeholder=plan.rollout_state == "placeholder",
        available_providers=("onnxruntime_cpu",),
    )
    return (
        load_gate.decide_load(candidate, "development", policy),
        load_gate.decide_load(candidate, "release", policy),
    )


# --------------------------------------------------------------------------
# downloading
# --------------------------------------------------------------------------


class TokenSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Drop Authorization when a redirect leaves the host it was meant for.

    Hugging Face answers a download with a redirect to a CDN host, and urllib
    copies the original request's headers onto the redirected request. Without
    this, every fetch of a gated model would hand the operator's token to
    whichever host the redirect named.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        if urllib.parse.urlsplit(newurl).netloc.lower() != urllib.parse.urlsplit(
            req.full_url
        ).netloc.lower():
            for name in list(new.headers):
                if name.lower() == "authorization":
                    del new.headers[name]
            new.unredirected_hdrs.pop("Authorization", None)
        return new


def opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(TokenSafeRedirectHandler)


def hf_token(environ: dict[str, str]) -> str | None:
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"):
        value = (environ.get(name) or "").strip()
        if value:
            return value
    return None


class Fetcher:
    """Streams a URL to a file, or explains why it could not."""

    def __init__(
        self,
        *,
        environ: dict[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        attempts: int = 3,
        sleep=time.sleep,
        build_opener=opener,
    ) -> None:
        self.environ = dict(os.environ if environ is None else environ)
        self.timeout = timeout
        self.attempts = max(1, attempts)
        self.sleep = sleep
        self.build_opener = build_opener

    def download(self, url: str, destination: Path) -> Payload:
        """Write `url` to `destination`. Raises Unavailable or Corrupt."""
        host = urllib.parse.urlsplit(url).netloc.lower()
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        token = hf_token(self.environ)
        if host.endswith("huggingface.co") and token:
            headers["Authorization"] = f"Bearer {token}"

        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            request = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with self.build_opener().open(request, timeout=self.timeout) as response:
                    return self._stream(response, destination)
            except urllib.error.HTTPError as error:
                last_error = error
                if error.code in {401, 403} and host.endswith("huggingface.co"):
                    raise Unavailable(
                        f"HTTP {error.code} from Hugging Face: "
                        + (
                            "a token is set and was refused -- the account may not "
                            "have accepted this model's terms"
                            if token
                            else "no HF_TOKEN in the environment; this repository "
                            "appears to be gated"
                        )
                    ) from error
                if error.code in {401, 403}:
                    raise Unavailable(f"HTTP {error.code}: access denied") from error
                if error.code == 404:
                    raise Unavailable(
                        "HTTP 404: the declared artifact URL is dead -- "
                        "the source moved or the file was renamed"
                    ) from error
                if error.code not in RETRYABLE_STATUS or attempt == self.attempts:
                    raise Unavailable(f"HTTP {error.code}: {error.reason}") from error
            except Truncated as error:
                last_error = error
                if attempt == self.attempts:
                    raise Unavailable(
                        f"{error} (after {self.attempts} attempt(s))"
                    ) from error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last_error = error
                if attempt == self.attempts:
                    raise Unavailable(f"network error: {error}") from error
            self.sleep(min(2.0 * attempt, 10.0))
        raise Unavailable(f"network error: {last_error}")  # pragma: no cover

    def _stream(self, response, destination: Path) -> Payload:
        blake3 = require_blake3()
        b3 = blake3()
        sha = hashlib.sha256()
        size = 0
        with destination.open("wb") as handle:
            while chunk := response.read(CHUNK):
                handle.write(chunk)
                b3.update(chunk)
                sha.update(chunk)
                size += len(chunk)
        declared = response.headers.get("Content-Length") if response.headers else None
        if declared is not None and declared.isdigit() and int(declared) != size:
            # A short read is the classic way a download becomes a plausible
            # file: the bytes are real, there are just fewer of them.
            raise Truncated(
                f"truncated download: Content-Length {declared}, received {size}"
            )
        return Payload(
            path=destination, byte_size=size, blake3=b3.hexdigest(), sha256=sha.hexdigest()
        )

    def read_text(self, url: str, limit: int = 4096) -> str | None:
        """Best-effort small GET, for the LFS pointer cross-check. None on any failure."""
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT}, method="GET"
        )
        try:
            with self.build_opener().open(request, timeout=self.timeout) as response:
                return response.read(limit).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - advisory check, never fatal on its own
            return None


# --------------------------------------------------------------------------
# content checks
# --------------------------------------------------------------------------


def artifact_url(source_url: str | None) -> str | None:
    """The URL to download, or None if the source is a provenance page."""
    if not source_url:
        return None
    split = urllib.parse.urlsplit(source_url.strip())
    if split.scheme not in {"http", "https"} or not split.netloc:
        return None
    if " " in source_url.strip():
        return None
    path = split.path.lower()
    segment = path.rsplit("/", 1)[-1]
    if any(segment.endswith(suffix) for suffix in ARTIFACT_SUFFIXES):
        return source_url.strip()
    if split.netloc.lower().endswith("huggingface.co") and "/resolve/" in path:
        return source_url.strip()
    return None


def check_payload_shape(path: Path, fmt: str) -> None:
    """Reject anything that is obviously not the declared artifact.

    Every rejection here corresponds to something a declared source in this
    registry actually served during development: GitHub's rate-limit body,
    GitHub's 404 HTML page, and a git-LFS pointer from raw.githubusercontent.
    All three are small, well-formed files that a fetcher without this check
    installs and hashes without complaint.
    """
    with path.open("rb") as handle:
        head = handle.read(512)
    if not head:
        raise Corrupt("empty file")
    if head.startswith(LFS_POINTER_PREFIX):
        raise Corrupt(
            "this is a git-LFS pointer, not the file it points at -- fetch it "
            "from media.githubusercontent.com/media/<owner>/<repo>/<ref>/<path>"
        )
    stripped = head.lstrip()
    if stripped[:1] in (b"<",) or stripped[:9].lower() == b"<!doctype":
        raise Corrupt("this is an HTML document, not a model file")
    if fmt == "onnx" and head[0] not in ONNX_LEADING_TAGS:
        raise Corrupt(
            f"does not begin like an ONNX ModelProto (first byte 0x{head[0]:02x})"
        )
    if fmt == "onnx" and head.startswith(b"PK\x03\x04"):
        raise Corrupt("this is a zip archive, not a bare .onnx file")


def lfs_pointer_url(url: str) -> str | None:
    """The raw.githubusercontent URL that holds the LFS pointer for `url`."""
    split = urllib.parse.urlsplit(url)
    if split.netloc.lower() != "media.githubusercontent.com":
        return None
    if not split.path.startswith("/media/"):
        return None
    return urllib.parse.urlunsplit(
        ("https", "raw.githubusercontent.com", split.path[len("/media") :], "", "")
    )


def parse_lfs_pointer(text: str) -> tuple[str, int] | None:
    """(sha256, size) from a git-LFS pointer, or None if this is not one."""
    if not text.startswith(LFS_POINTER_PREFIX.decode()):
        return None
    oid: str | None = None
    size: int | None = None
    for line in text.splitlines():
        if line.startswith("oid sha256:"):
            oid = line.split(":", 1)[1].strip()
        elif line.startswith("size "):
            candidate = line.split(" ", 1)[1].strip()
            if candidate.isdigit():
                size = int(candidate)
    if oid and len(oid) == 64 and size is not None:
        return oid, size
    return None


def corroborate(fetcher: Fetcher, url: str, payload: Payload) -> str:
    """Cross-check the bytes against the source repository's own record.

    Only git-LFS offers one today: the pointer committed in the repository
    carries the SHA-256 and size of the real object, so a download can be
    checked against something the maintainers wrote down rather than against
    itself. Where no such record exists, that is stated rather than implied.
    """
    pointer_url = lfs_pointer_url(url)
    if pointer_url is None:
        return "none available at this source"
    text = fetcher.read_text(pointer_url)
    if text is None:
        return "git-LFS pointer unreachable (advisory check skipped)"
    parsed = parse_lfs_pointer(text)
    if parsed is None:
        return "git-LFS pointer unreadable (advisory check skipped)"
    oid, size = parsed
    if oid != payload.sha256 or size != payload.byte_size:
        raise Corrupt(
            "bytes disagree with the git-LFS pointer committed at the source: "
            f"pointer sha256={oid} size={size}, downloaded sha256={payload.sha256} "
            f"size={payload.byte_size}"
        )
    return f"git-LFS pointer sha256 matches ({oid[:16]}..., {size} bytes)"


# --------------------------------------------------------------------------
# the fetch itself
# --------------------------------------------------------------------------


def extract_member(archive: Path, member: str, destination: Path) -> None:
    if not zipfile.is_zipfile(archive):
        raise Corrupt(f"archive_member declared but {archive.name} is not a zip archive")
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        if member not in names:
            raise Corrupt(
                f"archive member {member!r} is not in the archive; it contains "
                + ", ".join(sorted(names)[:8])
            )
        with bundle.open(member) as source, destination.open("wb") as target:
            shutil.copyfileobj(source, target, CHUNK)


def fetch_one(
    plan: Plan,
    *,
    fetcher: Fetcher,
    policy: dict,
    workspace: Path,
    archives: dict[str, Path],
    force: bool,
) -> Result:
    result = Result(model_id=plan.model_id, state=FAILED, detail="", config_path=plan.config_path)

    # Already installed? Decide without touching the network.
    if plan.install_path.is_file() and not force:
        payload = digest_file(plan.install_path)
        result.digest = payload.blake3
        result.byte_size = payload.byte_size
        if plan.pinned_hash is None:
            result.state = NEEDS_PIN
            result.detail = (
                "already installed, config has no pin -- not re-downloaded "
                "(nothing to compare against; use --force to replace)"
            )
        elif plan.pinned_hash == payload.blake3:
            result.state = VERIFIED
            result.detail = "already installed and matches its pin -- not re-downloaded"
        else:
            refusal = integrity_refusal(plan.pinned_hash, payload.blake3, policy)
            result.state = FAILED
            result.detail = (
                f"{refusal}: the installed file does not match its pin "
                f"(pinned {plan.pinned_hash[:16]}..., on disk {payload.blake3[:16]}...). "
                "Delete it and re-run; it is not being overwritten automatically."
            )
        return result

    url = artifact_url(plan.source_url)
    if url is None:
        result.state = UNAVAILABLE
        result.detail = (
            "no artifact URL: weights.source_url is "
            + (f"{plan.source_url!r}, which is a provenance page rather than a file"
               if plan.source_url
               else "null -- no checkpoint has been chosen")
        )
        return result

    # Rejected bytes are deleted rather than left in the staging directory.
    # The directory is removed at the end of a run anyway, but a run fetching
    # several gigabytes should not accumulate the ones it refused, and "nothing
    # rejected survives the decision to reject it" is easier to verify than
    # "something else cleans up later".
    staged = workspace / f"{plan.model_id}.{plan.filename}"
    try:
        archive_name = Path(urllib.parse.urlsplit(url).path).name
        if plan.archive_member:
            archive = archives.get(url)
            if archive is None:
                archive = workspace / f"archive-{len(archives)}-{archive_name}"
                fetcher.download(url, archive)
                archives[url] = archive
            extract_member(archive, plan.archive_member, staged)
            payload = digest_file(staged)
            check_payload_shape(staged, plan.fmt)
            result.corroboration = (
                f"extracted from member {plan.archive_member} of {archive_name}; "
                "the archive publishes no checksum, so these bytes are corroborated "
                "by nothing but the download itself"
            )
        else:
            payload = fetcher.download(url, staged)
            check_payload_shape(staged, plan.fmt)
            result.corroboration = corroborate(fetcher, url, payload)

        refusal = integrity_refusal(plan.pinned_hash, payload.blake3, policy)
        if refusal is not None:
            staged.unlink(missing_ok=True)
            result.state = FAILED
            result.detail = (
                f"{refusal}: downloaded bytes do not match the pin in "
                f"{plan.config_path.name} (pinned {plan.pinned_hash}, "
                f"downloaded {payload.blake3}). NOT installed."
            )
            result.digest = payload.blake3
            result.byte_size = payload.byte_size
            return result

        plan.install_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, plan.install_path)
        result.digest = payload.blake3
        result.byte_size = payload.byte_size
        if plan.pinned_hash is None:
            result.state = NEEDS_PIN
            result.detail = f"downloaded and installed from {url} -- config has no pin"
        else:
            result.state = VERIFIED
            result.detail = f"downloaded from {url} and matches its pin"
        return result
    except Unavailable as error:
        staged.unlink(missing_ok=True)
        result.state = UNAVAILABLE
        result.detail = str(error)
        return result
    except Corrupt as error:
        staged.unlink(missing_ok=True)
        result.state = FAILED
        result.detail = f"{url}: {error} -- NOT installed"
        return result
    except OSError as error:
        staged.unlink(missing_ok=True)
        result.state = FAILED
        result.detail = f"filesystem error: {error}"
        return result


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def exit_code(results: Iterable[Result]) -> int:
    """SUCCESS / PARTIAL / FAILURE, decided once and in one place."""
    scoped = [result for result in results if result.state != SKIPPED]
    if not scoped:
        return EXIT_FAILURE
    states = [result.state for result in scoped]
    if FAILED in states:
        return EXIT_FAILURE
    if all(state == VERIFIED for state in states):
        return EXIT_SUCCESS
    if not any(state in {VERIFIED, NEEDS_PIN} for state in states):
        # Nothing at all was obtained. "Partial" would be a lie.
        return EXIT_FAILURE
    return EXIT_PARTIAL


def report(results: list[Result], *, stream=sys.stdout) -> None:
    width = max((len(result.model_id) for result in results), default=0)
    for result in results:
        print(f"{result.model_id.ljust(width)}  {result.state:<12} {result.detail}", file=stream)
        if result.corroboration:
            print(f"{' ' * width}  {'':<12} corroboration: {result.corroboration}", file=stream)
        if result.digest:
            print(
                f"{' ' * width}  {'':<12} blake3 {result.digest}  ({result.byte_size} bytes)",
                file=stream,
            )
        if result.gate_development or result.gate_release:
            print(
                f"{' ' * width}  {'':<12} load gate: development="
                f"{result.gate_development or 'LOADABLE'} release="
                f"{result.gate_release or 'LOADABLE'}",
                file=stream,
            )

    unpinned = [result for result in results if result.state == NEEDS_PIN and result.digest]
    if unpinned:
        print(
            "\nUNPINNED. These digests were measured from what THIS run downloaded.\n"
            "They are not written to the registry, and nothing about them is checked\n"
            "by anyone but the operator: a fetcher pinning its own download would\n"
            "certify only that it hashed the bytes it just wrote. Read the source,\n"
            "decide the artifact is the right one, then paste into the config's\n"
            "weights block and re-run `python3 models/policy/digest.py --write`:\n",
            file=stream,
        )
        for result in unpinned:
            config = result.config_path.name if result.config_path else "<config>"
            print(f'  models/configs/{config}', file=stream)
            print(f'    "blake3": "{result.digest}",', file=stream)
            print(f'    "byte_size": {result.byte_size},', file=stream)

    counts: dict[str, int] = {}
    for result in results:
        counts[result.state] = counts.get(result.state, 0) + 1
    print(
        "\n" + "  ".join(f"{state}={count}" for state, count in sorted(counts.items())),
        file=stream,
    )


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fetch_weights.py",
        description=(
            "Download, verify and install the weights models/registry.json names. "
            "Exit 0 all verified, 3 partial, 4 failure, 5 cannot run."
        ),
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="MODEL_ID",
        help="fetch just this model (repeatable)",
    )
    parser.add_argument(
        "--weights-dir",
        type=Path,
        default=MODELS_ROOT / "weights",
        help="where to install (default: models/weights, what ml-runtime reads)",
    )
    parser.add_argument(
        "--include-placeholders",
        action="store_true",
        help="also attempt entries whose rollout.state is 'placeholder'",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-download even when a file is already installed",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    return parser.parse_args(argv)


def run(args: argparse.Namespace, *, fetcher: Fetcher | None = None, stream=sys.stdout) -> int:
    require_blake3()
    registry = read_json(REGISTRY_PATH)
    policy = registry.get("load_policy")
    if not isinstance(policy, dict):
        raise Precondition("models/registry.json has no load_policy")

    weights_dir = args.weights_dir.resolve()
    plans, skipped = build_plans(
        registry,
        MODELS_ROOT,
        weights_dir,
        only=list(args.only),
        include_placeholders=args.include_placeholders,
    )

    fetcher = fetcher or Fetcher(timeout=args.timeout)
    print(f"weights directory: {weights_dir}", file=stream)
    print(f"registry:          {REGISTRY_PATH}", file=stream)
    if not hf_token(fetcher.environ):
        print(
            "HF_TOKEN:          not set -- gated Hugging Face repositories will "
            "report UNAVAILABLE",
            file=stream,
        )
    print("", file=stream)

    results: list[Result] = []
    # Staged inside the destination directory so the final os.replace is an
    # atomic same-filesystem rename. A partially written download must never be
    # reachable under the name a model host loads by.
    weights_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=weights_dir, prefix=".staging-") as raw:
        workspace = Path(raw)
        archives: dict[str, Path] = {}
        for plan in plans:
            result = fetch_one(
                plan,
                fetcher=fetcher,
                policy=policy,
                workspace=workspace,
                archives=archives,
                force=args.force,
            )
            if result.state in {VERIFIED, NEEDS_PIN}:
                result.gate_development, result.gate_release = gate_verdicts(
                    plan, result.digest, policy
                )
            results.append(result)

    results.extend(skipped)
    report(results, stream=stream)
    return exit_code(results)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(parse_args(argv))
    except Precondition as error:
        print(f"cannot run: {error}", file=sys.stderr)
        return EXIT_PRECONDITION


if __name__ == "__main__":
    raise SystemExit(main())
